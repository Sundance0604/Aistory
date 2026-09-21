from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .tokens import TOKENIZER_VERSION, count_visible_tokens


VISIBLE_ROLES = {"user", "assistant"}
HIDDEN_CONTENT_TYPES = {
    "thoughts",
    "reasoning_recap",
    "user_editable_context",
    "model_editable_context",
    "computer_initialize_state",
}


def iso_utc(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def active_branch_ids(data: dict[str, Any]) -> list[str]:
    mapping = data.get("mapping") or {}
    current = data.get("current_node")
    if current in mapping:
        chain: list[str] = []
        seen: set[str] = set()
        while current and current in mapping and current not in seen:
            seen.add(current)
            chain.append(current)
            current = (mapping.get(current) or {}).get("parent")
        return list(reversed(chain))

    # Conservative fallback: select the deepest leaf, breaking ties by latest
    # visible timestamp and then stable node id.
    parent_ids = {node.get("parent") for node in mapping.values() if node.get("parent")}
    leaves = [node_id for node_id in mapping if node_id not in parent_ids] or list(mapping)

    def lineage(node_id: str) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            result.append(node_id)
            node_id = (mapping.get(node_id) or {}).get("parent")
        return list(reversed(result))

    candidates = [lineage(leaf) for leaf in leaves]
    return max(
        candidates,
        key=lambda chain: (
            len(chain),
            max(
                [((mapping[n].get("message") or {}).get("create_time") or 0) for n in chain],
                default=0,
            ),
            chain[-1] if chain else "",
        ),
        default=[],
    )


def _part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return ""
    if part.get("content_type") == "audio_transcription":
        return str(part.get("text") or "")
    if "text" in part and not part.get("asset_pointer"):
        return str(part.get("text") or "")
    return ""


def visible_text(message: dict[str, Any]) -> str:
    role = ((message.get("author") or {}).get("role") or "").lower()
    metadata = message.get("metadata") or {}
    content = message.get("content") or {}
    content_type = content.get("content_type") or ""
    if role not in VISIBLE_ROLES:
        return ""
    if metadata.get("is_visually_hidden_from_conversation"):
        return ""
    if message.get("recipient") not in (None, "all"):
        return ""
    if content_type in HIDDEN_CONTENT_TYPES:
        return ""
    if content_type in {"text", "multimodal_text"}:
        return "\n\n".join(filter(None, (_part_text(p) for p in content.get("parts") or []))).strip()
    if content_type == "code":
        return str(content.get("text") or "").strip()
    if content_type == "tether_quote":
        return "\n".join(filter(None, [content.get("title"), content.get("text")])).strip()
    return str(content.get("text") or "").strip()


@dataclass(frozen=True)
class NormalizedMessage:
    id: str
    conversation_id: str
    parent_id: str | None
    role: str
    created_at: str | None
    model: str | None
    content_type: str | None
    visible_text: str
    visible_tokens: int
    tokenizer_version: str
    content_hash: str
    sequence_index: int | None
    is_active_branch: bool
    has_attachment: bool
    raw_metadata_json: str


@dataclass(frozen=True)
class NormalizedConversation:
    id: str
    title: str
    created_at: str | None
    updated_at: str | None
    current_node_id: str | None
    content_hash: str
    archived: bool
    model_hint: str | None
    messages: tuple[NormalizedMessage, ...]


def normalize_conversation(data: dict[str, Any]) -> NormalizedConversation:
    conversation_id = data.get("conversation_id") or data.get("id")
    if not conversation_id:
        raise ValueError("conversation JSON has no conversation_id")
    mapping = data.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("conversation JSON has no non-empty mapping")

    active_ids = active_branch_ids(data)
    active_index = {node_id: index for index, node_id in enumerate(active_ids)}
    messages: list[NormalizedMessage] = []
    hash_messages: list[dict[str, Any]] = []
    for node_id, node in mapping.items():
        message = (node or {}).get("message")
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("id") or node_id)
        role = str(((message.get("author") or {}).get("role") or "unknown")).lower()
        content = message.get("content") or {}
        metadata = message.get("metadata") or {}
        text = visible_text(message)
        model = metadata.get("model_slug") or metadata.get("default_model_slug") or message.get("model")
        content_type = content.get("content_type")
        msg_hash = stable_hash({"role": role, "content_type": content_type, "visible_text": text})
        normalized = NormalizedMessage(
            id=message_id,
            conversation_id=str(conversation_id),
            parent_id=(node or {}).get("parent"),
            role=role,
            created_at=iso_utc(message.get("create_time")),
            model=str(model) if model else None,
            content_type=str(content_type) if content_type else None,
            visible_text=text,
            visible_tokens=count_visible_tokens(text, str(model) if model else None),
            tokenizer_version=TOKENIZER_VERSION,
            content_hash=msg_hash,
            sequence_index=active_index.get(node_id),
            is_active_branch=node_id in active_index,
            has_attachment=bool(metadata.get("attachments")) or any(
                isinstance(part, dict) and bool(part.get("asset_pointer"))
                for part in content.get("parts") or []
            ),
            raw_metadata_json=json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        )
        messages.append(normalized)
        hash_messages.append(
            {
                "id": normalized.id,
                "parent_id": normalized.parent_id,
                "role": normalized.role,
                "created_at": normalized.created_at,
                "content_hash": normalized.content_hash,
                "is_active_branch": normalized.is_active_branch,
            }
        )
    content_hash = stable_hash(
        {
            "title": data.get("title"),
            "current_node": data.get("current_node"),
            "messages": sorted(hash_messages, key=lambda item: item["id"]),
        }
    )
    return NormalizedConversation(
        id=str(conversation_id),
        title=str(data.get("title") or "Untitled"),
        created_at=iso_utc(data.get("create_time")),
        updated_at=iso_utc(data.get("update_time")),
        current_node_id=data.get("current_node"),
        content_hash=content_hash,
        archived=bool(data.get("is_archived")),
        model_hint=data.get("default_model_slug"),
        messages=tuple(messages),
    )
