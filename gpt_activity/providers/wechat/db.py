"""The only adapter that constructs WeChatDB; all upstream SQL stays read-only."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


MESSAGE_FIELDS = [
    "local_id", "local_type", "real_sender_id", "create_time", "message_content",
    "source", "packed_info_data", "compress_content", "sort_seq",
]


def _wechat_api():
    try:
        from wechatauto import WeChatDB, auto_detect_db_dir, list_accounts
    except ImportError as exc:
        raise RuntimeError(
            "WeChat provider dependency unavailable. Install Aistory with the 'wechat' extra."
        ) from exc
    return WeChatDB, auto_detect_db_dir, list_accounts


def _normalise_location(db_dir=None, account: str | None = None) -> tuple[str | None, str | None]:
    """Accept a discovery root, an account directory, or a db_storage directory."""
    if not db_dir:
        return None, account or None
    path = Path(db_dir).expanduser().resolve()
    if path.name.lower() == "db_storage":
        return str(path.parent.parent), account or path.parent.name
    if (path / "db_storage").is_dir():
        return str(path.parent), account or path.name
    return str(path), account or None


def _process_data_root() -> str | None:
    """Find custom storage placed next to the running Weixin installation."""
    try:
        import psutil
    except ImportError:
        return None
    candidates: list[Path] = []
    for process in psutil.process_iter(["name", "exe"]):
        try:
            if str(process.info.get("name") or "").lower() not in {"weixin.exe", "wechat.exe"}:
                continue
            executable = process.info.get("exe")
            if not executable:
                continue
            install = Path(str(executable)).resolve().parent
            candidates.extend([
                install / "xwechat_files",
                install.parent / "xwechat_files",
                install.parent / "chat" / "xwechat_files",
                install.parent / "data" / "xwechat_files",
            ])
        except (OSError, psutil.Error):
            continue
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_dir() and any((child / "db_storage").is_dir() for child in candidate.iterdir() if child.is_dir()):
            return str(candidate)
    return None


def open_wechat(db_dir=None, account: str | None = None):
    WeChatDB, _, _ = _wechat_api()
    root, selected = _normalise_location(db_dir, account)
    return WeChatDB(db_dir=root, account=selected)


def discover_wechat_accounts(db_dir=None) -> dict[str, Any]:
    """Find local WeChat 4.x accounts without opening or modifying their databases."""
    _, auto_detect_db_dir, list_accounts = _wechat_api()
    root, selected = _normalise_location(db_dir)
    root = root or auto_detect_db_dir() or _process_data_root()
    if not root:
        return {
            "available": True,
            "detected_root": None,
            "accounts": [],
            "message": "未自动找到微信 4.x 数据目录。请先登录桌面微信，或填写数据根目录后重试。",
        }
    discovered = []
    for item in list_accounts(root):
        account = str(item.get("account") or "").strip()
        if selected and account != selected:
            continue
        wxid = str(item.get("wxid") or re.sub(r"_\w{4}$", "", account)).strip()
        suggested = hashlib.sha256((wxid or account).encode("utf-8")).hexdigest()[:10]
        discovered.append({
            "account": account,
            "wxid": wxid,
            "path": str(item.get("path") or ""),
            "data_dir": str(root),
            "last_activity": item.get("last_activity"),
            "suggested_id": f"wechat_{suggested}",
        })
    return {
        "available": True,
        "detected_root": str(root),
        "accounts": discovered,
        "message": None if discovered else "目录存在，但没有发现包含 db_storage 的微信账号。",
    }


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
