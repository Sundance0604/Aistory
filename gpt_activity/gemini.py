from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .db import connect, ensure_account
from .importer import upsert_conversation


@dataclass
class GeminiSyncResult:
    discovered: int = 0
    fetched: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    messages: int = 0
    failed_cids: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timestamp(value: Any) -> float | None:
    if isinstance(value, list) and len(value) >= 2:
        try:
            return float(value[0]) + float(value[1]) / 1_000_000_000
        except (TypeError, ValueError):
            return None
    return None


def _iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def _error_category(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "auth" in name or "cookie" in message or "401" in message or "403" in message:
        return "auth"
    if "rate" in name or "limit" in name or "429" in message:
        return "rate_limit"
    if "timeout" in name or "network" in name or "proxy" in name:
        return "network_proxy"
    if "json" in name or "parse" in name or "schema" in message:
        return "parse_schema"
    if "rpc" in message:
        return "rpc"
    if "not found" in message or "missing" in message:
        return "missing"
    return "unknown"


async def _rpc_body(client, rpc_id, payload: list[Any]) -> Any:
    from gemini_webapi.types import RPCData
    from gemini_webapi.utils import extract_json_from_response, get_nested_value

    response = await client._batch_execute([RPCData(rpcid=rpc_id, payload=json.dumps(payload))])
    for part in extract_json_from_response(response.text):
        encoded = get_nested_value(part, [2])
        if encoded:
            return json.loads(encoded)
    raise RuntimeError(f"Gemini RPC {rpc_id} returned no parseable body")


async def _list_category(client, category: int, page_size: int) -> list[dict[str, Any]]:
    from gemini_webapi.constants import GRPC
    from gemini_webapi.utils import get_nested_value

    cursor = None
    result: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()
    while True:
        body = await _rpc_body(client, GRPC.LIST_CONVERSATIONS, [page_size, cursor, [category, None, 1]])
        for raw in get_nested_value(body, [2], []) or []:
            cid = get_nested_value(raw, [0], "")
            if not cid:
                continue
            result.append({
                "cid": cid,
                "title": get_nested_value(raw, [1], "") or "Untitled",
                "pinned": bool(get_nested_value(raw, [2])),
                "timestamp": _timestamp(get_nested_value(raw, [5])),
            })
        next_cursor = get_nested_value(body, [1])
        if not next_cursor or str(next_cursor) in seen_cursors:
            break
        seen_cursors.add(str(next_cursor))
        cursor = next_cursor
    return result


async def _read_conversation(client, chat: dict[str, Any], limit: int) -> dict[str, Any]:
    from gemini_webapi.constants import GRPC
    from gemini_webapi.utils import get_nested_value

    body = await _rpc_body(client, GRPC.LIST_CONVERSATION_TURNS, [chat["cid"], limit, None, 1, [1], [4], None, 1])
    turns = get_nested_value(body, [0], []) or []
    mapping: dict[str, Any] = {}
    parent: str | None = None
    timestamps: list[float] = []
    for turn_index, conv_turn in enumerate(reversed(turns)):
        rid = str(get_nested_value(conv_turn, [0, 1], "") or f"turn-{turn_index}")
        stamp = _timestamp(get_nested_value(conv_turn, [4]))
        if stamp is not None:
            timestamps.append(stamp)
        candidates = get_nested_value(conv_turn, [3, 0], []) or []
        user_text = str(get_nested_value(conv_turn, [2, 0, 0], "") or "")
        user_id = f"{chat['cid']}:{rid}:user"
        non_text = not user_text and bool(candidates)
        mapping[user_id] = {
            "id": user_id,
            "parent": parent,
            "message": {
                "id": user_id,
                "author": {"role": "user"},
                "create_time": stamp,
                "recipient": "all",
                "content": {"content_type": "multimodal_text" if non_text else "text", "parts": ([{"asset_pointer": "gemini-non-text"}] if non_text else [user_text])},
                "metadata": {"provider": "gemini", "rid": rid, "attachments": ([{"kind": "non_text"}] if non_text else [])},
            },
            "children": [],
        }
        if parent and parent in mapping:
            mapping[parent]["children"].append(user_id)
        parent = user_id
        if candidates:
            candidate = candidates[0]
            rcid = str(get_nested_value(candidate, [0], "") or f"candidate-{turn_index}")
            text, *_ = client._parse_candidate(candidate, chat["cid"], rid, rcid)
            model_id = f"{chat['cid']}:{rid}:{rcid}"
            mapping[model_id] = {
                "id": model_id,
                "parent": parent,
                "message": {
                    "id": model_id,
                    "author": {"role": "assistant"},
                    "create_time": stamp,
                    "recipient": "all",
                    "content": {"content_type": "text", "parts": [text or ""]},
                    "metadata": {"provider": "gemini", "rid": rid, "rcid": rcid, "model_slug": "Gemini"},
                },
                "children": [],
            }
            mapping[parent]["children"].append(model_id)
            parent = model_id
    return {
        "conversation_id": chat["cid"],
        "title": chat["title"],
        "create_time": min(timestamps) if timestamps else chat.get("timestamp"),
        "update_time": chat.get("timestamp") or (max(timestamps) if timestamps else None),
        "current_node": parent,
        "mapping": mapping,
        "is_archived": False,
        "model_slug": "Gemini",
    }


async def _sync(settings: Settings, account: dict[str, Any], force_fetch: bool) -> GeminiSyncResult:
    try:
        from gemini_webapi import GeminiClient
    except ImportError as exc:
        raise RuntimeError("缺少 gemini-webapi；请在 pavane 环境中运行") from exc

    psid, psidts, proxy = settings.gemini_credentials
    if not psid:
        raise RuntimeError("Gemini 凭据未配置")
    config = settings.values.get("gemini", {})
    client = GeminiClient(psid, psidts or None, proxy=proxy)
    result = GeminiSyncResult()
    account_id = account["id"]
    ensure_account(settings.database_path, account_id, account.get("name") or account_id, "gemini")
    try:
        await client.init(timeout=int(config.get("request_timeout_seconds", 60)), auto_close=False, auto_refresh=False)
        page_size = min(100, int(config.get("page_size", 100)))
        regular = await _list_category(client, 0, page_size)
        pinned = await _list_category(client, 1, page_size)
        chats = list({chat["cid"]: chat for chat in [*regular, *pinned]}.values())
        chats.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
        result.discovered = len(chats)
        with connect(settings.database_path) as conn:
            existing = {row["remote_id"]: row["updated_at"] for row in conn.execute("SELECT remote_id,updated_at FROM conversations WHERE provider='gemini' AND account_id=?", (account_id,))}
        recent_count = int(config.get("recent_refetch_count", 30))
        retry_delays = [float(item) for item in config.get("retry_delays_seconds", [1, 3, 10])]
        for index, chat in enumerate(chats):
            remote_updated = _iso(chat.get("timestamp"))
            if not force_fetch and index >= recent_count and chat["cid"] in existing and existing[chat["cid"]] == remote_updated:
                result.unchanged += 1
                continue
            error: Exception | None = None
            for attempt in range(len(retry_delays) + 1):
                try:
                    data = await _read_conversation(client, chat, int(config.get("read_limit", 10000)))
                    state, _ = upsert_conversation(settings, data, account_id=account_id, account_name=account.get("name") or account_id, provider="gemini", keep_raw=True)
                    setattr(result, state, getattr(result, state) + 1)
                    result.fetched += 1
                    result.messages += len(data["mapping"])
                    error = None
                    break
                except Exception as exc:
                    error = exc
                    if attempt < len(retry_delays):
                        await asyncio.sleep(retry_delays[attempt])
            if error:
                result.failed += 1
                result.failed_cids.append(chat["cid"])
                category = _error_category(error)
                result.failures.append({"cid": chat["cid"], "category": category, "error_type": type(error).__name__})
                print(f"[GEMINI] {chat['cid']}: failed ({category}/{type(error).__name__})", flush=True)
    finally:
        await client.close()
    return result


def run_gemini_sync(settings: Settings, account: dict[str, Any], force_fetch: bool = False) -> dict[str, Any]:
    return _sync_runner(_sync(settings, account, force_fetch)).as_dict()


def _sync_runner(coro):
    return asyncio.run(coro)
