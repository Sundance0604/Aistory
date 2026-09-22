from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from zipfile import ZipFile

from .config import Settings
from .db import connect, ensure_account, migrate
from .parser import NormalizedConversation, normalize_conversation
from .storage import raw_storage, safe_segment


@dataclass
class ImportResult:
    seen: int = 0
    imported: int = 0
    unchanged: int = 0
    failed: int = 0
    messages: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def discover_json_files(path: str | Path) -> list[Path]:
    source = Path(path)
    if source.is_file():
        return [source]
    files = list(source.rglob("conversation.json"))
    files.extend(source.rglob("conversations.json"))
    files.extend(source.glob("*.zip"))
    return sorted(set(files))


def _payloads(value: object, label: str) -> Iterator[tuple[str, dict]]:
    if isinstance(value, dict) and isinstance(value.get("mapping"), dict):
        yield label, value
    elif isinstance(value, list):
        for index, item in enumerate(value, 1):
            if isinstance(item, dict) and isinstance(item.get("mapping"), dict):
                yield f"{label}#{index}", item


def iter_conversation_payloads(path: str | Path) -> Iterator[tuple[str, dict]]:
    for source in discover_json_files(path):
        if source.suffix.lower() == ".zip":
            with ZipFile(source) as archive:
                for name in archive.namelist():
                    lower = name.lower()
                    if not (lower.endswith("/conversation.json") or lower.endswith("/conversations.json") or lower in {"conversation.json", "conversations.json"}):
                        continue
                    value = json.loads(archive.read(name).decode("utf-8"))
                    yield from _payloads(value, f"{source}!{name}")
        else:
            value = json.loads(source.read_text(encoding="utf-8"))
            yield from _payloads(value, str(source))


def _storage_id(account_id: str, remote_id: str, provider: str = "chatgpt") -> str:
    prefix = "" if provider == "chatgpt" else f"{safe_segment(provider)}::"
    account = "" if account_id == "default" else f"{safe_segment(account_id)}::"
    return f"{prefix}{account}{remote_id}"


def upsert_conversation(
    settings: Settings,
    data: dict,
    *,
    source_url: str | None = None,
    project_id: str | None = None,
    keep_raw: bool = True,
    account_id: str = "default",
    account_name: str | None = None,
    provider: str = "chatgpt",
) -> tuple[str, int]:
    """Return (new|updated|unchanged, inserted active prompt count)."""
    migrate(settings.database_path)
    ensure_account(settings.database_path, account_id, account_name or account_id, provider)
    normalized = normalize_conversation(data)
    conversation_id = _storage_id(account_id, normalized.id, provider)
    fetched_at = datetime.now(timezone.utc).isoformat()
    raw_path = raw_storage(settings).write(account_id, normalized.id, data) if keep_raw else None

    with connect(settings.database_path) as conn:
        existing = conn.execute(
            "SELECT id,content_hash FROM conversations WHERE provider=? AND account_id=? AND remote_id=?",
            (provider, account_id, normalized.id),
        ).fetchone()
        if existing and existing["content_hash"] == normalized.content_hash:
            conn.execute(
                "UPDATE conversations SET fetched_at=?, updated_at=COALESCE(?, updated_at), "
                "title=?, raw_json_path=COALESCE(?, raw_json_path) WHERE id=?",
                (
                    fetched_at,
                    normalized.updated_at,
                    normalized.title,
                    str(raw_path) if raw_path else None,
                    existing["id"],
                ),
            )
            return "unchanged", 0

        state = "updated" if existing else "new"
        conn.execute(
            """
            INSERT INTO conversations(
              id,account_id,provider,remote_id,title,created_at,updated_at,fetched_at,current_node_id,content_hash,
              archived,model_hint,raw_json_path,source_url,project_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title, created_at=excluded.created_at,
              updated_at=excluded.updated_at, fetched_at=excluded.fetched_at,
              current_node_id=excluded.current_node_id, content_hash=excluded.content_hash,
              archived=excluded.archived, model_hint=excluded.model_hint,
              raw_json_path=excluded.raw_json_path,
              source_url=COALESCE(excluded.source_url, conversations.source_url),
              project_id=COALESCE(excluded.project_id, conversations.project_id)
            """,
            (
                conversation_id,
                account_id,
                provider,
                normalized.id,
                normalized.title,
                normalized.created_at,
                normalized.updated_at,
                fetched_at,
                normalized.current_node_id,
                normalized.content_hash,
                int(normalized.archived),
                normalized.model_hint,
                str(raw_path) if raw_path else None,
                source_url,
                project_id,
            ),
        )
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
        conn.executemany(
            """
            INSERT INTO messages(
              id,conversation_id,parent_id,role,created_at,model,content_type,
              visible_text,visible_tokens,tokenizer_version,content_hash,
              sequence_index,is_active_branch,has_attachment,analyzable,raw_metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    _storage_id(account_id, message.id, provider),
                    conversation_id,
                    _storage_id(account_id, message.parent_id, provider) if message.parent_id else None,
                    message.role,
                    message.created_at,
                    message.model,
                    message.content_type,
                    message.visible_text,
                    message.visible_tokens,
                    message.tokenizer_version,
                    message.content_hash,
                    message.sequence_index,
                    int(message.is_active_branch),
                    int(message.has_attachment),
                    int(message.analyzable),
                    message.raw_metadata_json,
                )
                for message in normalized.messages
            ],
        )
        prompts = sum(
            1 for message in normalized.messages if message.is_active_branch and message.role == "user"
        )
        return state, prompts


def import_json(
    settings: Settings,
    source: str | Path,
    *,
    keep_raw: bool = True,
    account_id: str = "default",
    account_name: str | None = None,
    provider: str = "chatgpt",
) -> ImportResult:
    result = ImportResult()
    payloads = list(iter_conversation_payloads(source))
    if not payloads:
        raise ValueError(f"No ChatGPT conversations found in {source}")
    print(f"[IMPORT] {len(payloads)} conversation(s) found", flush=True)
    for index, (label, data) in enumerate(payloads, 1):
        result.seen += 1
        try:
            state, prompts = upsert_conversation(
                settings,
                data,
                keep_raw=keep_raw,
                account_id=account_id,
                account_name=account_name,
                provider=provider,
            )
            if state == "unchanged":
                result.unchanged += 1
            else:
                result.imported += 1
                result.messages += len((data.get("mapping") or {}))
            print(f"[IMPORT {index}/{len(payloads)}] {data.get('title') or label}: {state}", flush=True)
        except Exception as exc:
            result.failed += 1
            print(f"[IMPORT {index}/{len(payloads)}] {label}: failed ({type(exc).__name__}: {exc})", flush=True)
    imported_at = datetime.now(timezone.utc).isoformat()
    ensure_account(settings.database_path, account_id, account_name or account_id, provider)
    with connect(settings.database_path) as conn:
        conn.execute(
            """
            INSERT INTO data_sources(
              account_id,kind,location,storage_method,imported_at,
              items_seen,items_imported,items_failed
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                account_id,
                f"{provider}-export-zip" if str(source).lower().endswith(".zip") else f"{provider}-export-json",
                str(Path(source).resolve()),
                settings.values["storage"].get("method", "filesystem"),
                imported_at,
                result.seen,
                result.imported,
                result.failed,
            ),
        )
    print(
        f"[IMPORT] complete imported={result.imported} unchanged={result.unchanged} failed={result.failed}",
        flush=True,
    )
    from .analytics import refresh_analytics
    from .usage_time import refresh_usage_time
    refresh_analytics(settings.database_path, settings.timezone, settings.values.get("analytics", {}))
    refresh_usage_time(settings.database_path, settings.values.get("usage_time", {}))
    return result
