"""The only adapter that constructs WeChatDB; all upstream SQL stays read-only."""

from __future__ import annotations

import re


MESSAGE_FIELDS = [
    "local_id", "local_type", "real_sender_id", "create_time", "message_content",
    "source", "packed_info_data", "compress_content", "sort_seq",
]


def open_wechat(db_dir=None):
    try:
        from wechatauto import WeChatDB
    except ImportError as exc:
        raise RuntimeError(
            "WeChat provider dependency unavailable. Install Aistory with the 'wechat' extra."
        ) from exc
    return WeChatDB(db_dir=str(db_dir) if db_dir else None)


def read_message_rows(conn, table: str, cursor=None):
    if not re.fullmatch(r"Msg_[a-fA-F0-9]{32}", table):
        raise ValueError("Invalid message table name")
    existing = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    if "local_id" not in existing:
        raise ValueError("Message table lacks a stable local_id; checkpoint not advanced")
    parts = [f'"{field}"' if field in existing else f'NULL AS "{field}"' for field in MESSAGE_FIELDS]
    where: list[str] = []
    params: list[object] = []
    mode = "sort_seq" if "sort_seq" in existing else "local_id"
    if cursor:
        if cursor.get("cursor_mode", mode) != mode:
            raise ValueError("Message schema changed; perform an explicit historical backfill")
        if mode == "sort_seq":
            where.append("(COALESCE(sort_seq,0) > ? OR (COALESCE(sort_seq,0) = ? AND local_id > ?))")
            params.extend([cursor.get("last_sort_seq", 0), cursor.get("last_sort_seq", 0), cursor.get("last_local_id", 0)])
        else:
            where.append("local_id > ?")
            params.append(cursor.get("last_local_id", 0))
    sql = f'SELECT {", ".join(parts)} FROM "{table}"'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + ("COALESCE(sort_seq,0), " if mode == "sort_seq" else "") + "local_id"
    query = conn.execute(sql, params)
    columns = [column[0] for column in query.description]
    return [dict(zip(columns, row)) for row in query], mode
