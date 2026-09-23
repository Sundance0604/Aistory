from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from ...db import connect, ensure_account, migrate
from ...tokens import TOKENIZER_VERSION, count_visible_tokens
from .common import md5_username, normalize_sender_id, stable_id, table_exists
from .db import open_wechat, read_message_rows
from .message_parser import clean_content, get_base_type
from .resolver import WeChatSource, discover_sources
from .sender_resolver import explicit_sender_from_row, filehelper_self_ids


def _timestamp(value: object) -> str:
    stamp = float(value)
    if stamp > 10_000_000_000:
        stamp /= 1000
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def _load_checkpoint(db_path, account_id: str, source_id: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT state_json FROM provider_sync_state WHERE provider='wechat' AND account_id=? AND source_id=?",
            (account_id, source_id),
        ).fetchone()
    if not row:
        return {"source_id": source_id, "shards": {}}
    try:
        value = json.loads(row["state_json"])
        return value if isinstance(value, dict) else {"source_id": source_id, "shards": {}}
    except json.JSONDecodeError:
        return {"source_id": source_id, "shards": {}}


def _account_identity(db) -> tuple[str, str, dict[str, Any]]:
    info = db.get_self_info() or {}
    wxid = str(info.get("username") or info.get("wxid") or "").strip()
    if not wxid:
        raise RuntimeError("Unable to read self wxid from the WeChat database")
    name = str(info.get("remark") or info.get("nickname") or info.get("nick_name") or wxid)
    return wxid, name, info


def _sync_source(
    settings, db, account: dict[str, Any], source: WeChatSource, names: dict[str, str],
    self_wxid: str, message_connections: dict[str, Any], self_sender_ids: dict[str, set[str]],
    *, force_fetch: bool,
) -> dict[str, Any]:
    account_id = str(account["id"])
    checkpoint = {"source_id": source.remote_id, "shards": {}} if force_fetch else _load_checkpoint(
        settings.database_path, account_id, source.remote_id
    )
    conversation_id = stable_id("wechat", account_id, source.remote_id)
    known_ids = set(names) | {source.remote_id, self_wxid}
    if source.conversation_type == "group":
        try:
            for member in db.get_group_members(source.remote_id) or []:
                wxid = str(member.get("username") or "")
                if wxid:
                    names[wxid] = str(member.get("remark") or member.get("nick_name") or wxid)
                    known_ids.add(wxid)
        except Exception:
            pass

    table = "Msg_" + md5_username(source.remote_id)
    normalized: list[dict[str, Any]] = []
    for rel, conn in message_connections.items():
        shard = str(rel)
        if not table_exists(conn, table):
            continue
        previous = checkpoint.setdefault("shards", {}).get(shard)
        rows, mode = read_message_rows(conn, table, previous)
        votes: dict[str, Counter] = defaultdict(Counter)
        if previous:
            for sender_id, counts in (previous.get("sender_votes") or {}).items():
                votes[sender_id].update(counts)
        for row in rows:
            sender_id = normalize_sender_id(row.get("real_sender_id"))
            sender = explicit_sender_from_row(row, known_ids, source.remote_id)
            if sender_id not in {"", "0", "None"} and sender:
                votes[sender_id][sender] += 1
        sender_map = {
            sender_id: ranking[0][0]
            for sender_id, counts in votes.items()
            if (ranking := counts.most_common()) and (len(ranking) == 1 or ranking[0][1] > ranking[1][1])
        }
        shard_self_sender_ids = self_sender_ids.get(shard, set())
        for sender_id in shard_self_sender_ids:
            sender_map.setdefault(sender_id, self_wxid)

        for row in rows:
            sender_id = normalize_sender_id(row.get("real_sender_id"))
            sender = explicit_sender_from_row(row, known_ids, source.remote_id) or sender_map.get(sender_id)
            base_type = get_base_type(row.get("local_type"))
            if base_type == 10000:
                direction, role, sender, sender_name = "system", "system", "", "系统"
            else:
                if not sender and source.conversation_type == "private":
                    sender = self_wxid if sender_id in shard_self_sender_ids else source.remote_id
                direction = "outbound" if sender == self_wxid else "inbound"
                role = "user" if direction == "outbound" else "assistant"
                sender_name = "我" if direction == "outbound" else names.get(sender or "", sender or f"未识别成员#{sender_id}")
            text = clean_content(row, known_ids, source.remote_id)
            created_at = _timestamp(row.get("create_time"))
            local_id = int(row["local_id"])
            sort_seq = int(row.get("sort_seq") or 0)
            normalized.append({
                "id": stable_id("wechat", account_id, source.remote_id, shard, local_id),
                "role": role, "direction": direction, "sender_external_id": sender or None,
                "sender_display_name": sender_name, "created_at": created_at,
                "visible_text": text, "visible_tokens": count_visible_tokens(text),
                "content_type": "wechat_message", "raw_type": str(row.get("local_type")),
                "sequence_index": sort_seq or int(datetime.fromisoformat(created_at).timestamp() * 1000),
                "metadata": {"shard": shard, "sort_seq": sort_seq, "local_id": local_id, "sender_id": sender_id, "sender_wxid": sender or ""},
            })
        if rows:
            last = rows[-1]
            checkpoint["shards"][shard] = {
                "last_sort_seq": int(last.get("sort_seq") or 0),
                "last_local_id": int(last["local_id"]),
                "cursor_mode": mode,
                "sender_votes": {sender_id: dict(counts) for sender_id, counts in votes.items()},
            }

    normalized.sort(key=lambda item: (item["created_at"], item["sequence_index"], item["id"]))
    now = datetime.now(timezone.utc).isoformat()
    first = normalized[0]["created_at"] if normalized else None
    last = normalized[-1]["created_at"] if normalized else None
    with connect(settings.database_path) as conn:
        existing = conn.execute("SELECT id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not normalized and existing is None:
            return {"new": 0, "updated": 0, "unchanged": 0, "messages": 0}
        conn.execute(
            """INSERT INTO conversations(
                 id,account_id,provider,remote_id,title,created_at,updated_at,fetched_at,
                 content_hash,archived,model_hint,conversation_type
               ) VALUES(?,?,?,?,?,?,?,?,?,0,NULL,?)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                 created_at=COALESCE(conversations.created_at,excluded.created_at),
                 updated_at=COALESCE(excluded.updated_at,conversations.updated_at),
                 fetched_at=excluded.fetched_at,conversation_type=excluded.conversation_type""",
            (conversation_id, account_id, "wechat", source.remote_id, source.title, first, last, now,
             stable_id("wechat", account_id, source.remote_id, last or "empty"), source.conversation_type),
        )
        inserted = 0
        for item in normalized:
            values = (
                item["id"], conversation_id, item["role"], item["created_at"], item["content_type"],
                item["visible_text"], item["visible_tokens"], TOKENIZER_VERSION,
                stable_id(item["visible_text"], item["raw_type"]), item["sequence_index"],
                json.dumps(item["metadata"], ensure_ascii=False), item["direction"],
                item["sender_external_id"], item["sender_display_name"], item["raw_type"],
            )
            cursor = conn.execute(
                """INSERT INTO messages(
                     id,conversation_id,parent_id,role,created_at,model,content_type,visible_text,
                     visible_tokens,tokenizer_version,content_hash,sequence_index,is_active_branch,
                     has_attachment,analyzable,raw_metadata_json,direction,sender_external_id,
                     sender_display_name,raw_type
                   ) VALUES(?,?,NULL,?,?,NULL,?,?,?,?,?,?,1,0,0,?,?,?,?,?)
                   ON CONFLICT(id) DO NOTHING""",
                values,
            )
            if cursor.rowcount:
                inserted += 1
            else:
                conn.execute(
                    """UPDATE messages SET visible_text=?,visible_tokens=?,content_hash=?,sequence_index=?,
                       raw_metadata_json=?,direction=?,sender_external_id=?,sender_display_name=?,raw_type=?
                       WHERE id=?""",
                    (item["visible_text"], item["visible_tokens"], values[8], item["sequence_index"],
                     values[10], item["direction"], item["sender_external_id"],
                     item["sender_display_name"], item["raw_type"], item["id"]),
                )
        checkpoint["last_collected_at"] = now
        conn.execute(
            """INSERT INTO provider_sync_state(provider,account_id,source_id,state_json,updated_at)
               VALUES('wechat',?,?,?,?) ON CONFLICT(provider,account_id,source_id)
               DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at""",
            (account_id, source.remote_id, json.dumps(checkpoint, ensure_ascii=False), now),
        )
    return {"new": int(existing is None), "updated": int(existing is not None and bool(normalized)), "unchanged": int(existing is not None and not normalized), "messages": inserted}


def run_wechat_sync(settings, account: dict[str, Any], *, force_fetch: bool = False) -> dict[str, Any]:
    migrate(settings.database_path)
    account_id = str(account["id"])
    data_dir = settings.wechat_data_dir_for(account)
    selected_account = settings.wechat_account_for(account)
    db = open_wechat(data_dir, selected_account)
    self_wxid, detected_name, self_info = _account_identity(db)
    with connect(settings.database_path) as conn:
        row = conn.execute("SELECT external_user_id FROM accounts WHERE id=?", (account_id,)).fetchone()
    if row and row["external_user_id"] and row["external_user_id"] != self_wxid:
        raise RuntimeError("当前数据库目录似乎属于另一个微信账号。")
    account_name = str(account.get("name") or detected_name or account_id)
    ensure_account(
        settings.database_path, account_id, account_name, "wechat",
        alias=str(account.get("alias") or "") or None,
        external_user_id=self_wxid,
        metadata_json=json.dumps({
            "wechat_data_dir": str(getattr(db, "db_dir", data_dir) or ""),
            "wechat_account": str(getattr(db, "account", selected_account) or ""),
            "self_info": self_info,
        }, ensure_ascii=False, default=str),
    )
    provider_options = settings.values.get("wechat", {})
    message_connections: dict[str, Any] = {}
    try:
        for rel in db._message_dbs():
            conn = db._open(rel)
            conn.execute("PRAGMA query_only=ON")
            message_connections[str(rel)] = conn
        sources, names = discover_sources(
            db, self_wxid=self_wxid,
            sync_private=bool(provider_options.get("sync_private", True)),
            sync_groups=bool(provider_options.get("sync_groups", True)),
            message_connections=message_connections,
        )
        self_sender_ids = {rel: filehelper_self_ids(conn) for rel, conn in message_connections.items()}
        totals = {"discovered": len(sources), "new": 0, "updated": 0, "unchanged": 0, "failed": 0, "messages": 0}
        failures: list[dict[str, str]] = []
        for source in sources:
            try:
                result = _sync_source(
                    settings, db, account, source, names, self_wxid,
                    message_connections, self_sender_ids, force_fetch=force_fetch,
                )
                for key in ("new", "updated", "unchanged", "messages"):
                    totals[key] += result[key]
            except Exception as exc:
                totals["failed"] += 1
                failures.append({"remote_id": source.remote_id, "title": source.title, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        for conn in message_connections.values():
            conn.close()
    return {**totals, "self_wxid": self_wxid, "failures": failures}
