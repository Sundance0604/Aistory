from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass

from .common import md5_username


@dataclass(frozen=True)
class WeChatSource:
    remote_id: str
    title: str
    conversation_type: str


def load_contacts(db) -> dict[str, dict[str, str]]:
    contacts: dict[str, dict[str, str]] = {}
    for rel, path, _ in db._db_files:
        if os.path.basename(path).lower() != "contact.db":
            continue
        conn = db._open(rel)
        try:
            conn.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in conn.execute('PRAGMA table_info("contact")')}
            if "username" not in columns:
                return contacts
            selected = [field for field in ("username", "nick_name", "remark") if field in columns]
            for row in conn.execute(f'SELECT {", ".join(selected)} FROM "contact"'):
                item = dict(zip(selected, row))
                username = str(item.get("username") or "")
                if username:
                    contacts[username] = {
                        "username": username,
                        "nick_name": str(item.get("nick_name") or ""),
                        "remark": str(item.get("remark") or ""),
                    }
        finally:
            conn.close()
        break
    return contacts


def _message_tables(db, message_connections=None) -> set[str]:
    tables: set[str] = set()
    for rel in db._message_dbs():
        key = str(rel)
        conn = message_connections[key] if message_connections is not None else db._open(rel)
        try:
            conn.execute("PRAGMA query_only=ON")
            tables.update(
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'Msg_[0-9A-Fa-f]*'"
                )
            )
        finally:
            if message_connections is None:
                conn.close()
    return tables


def _display(contact: dict[str, str] | None, fallback: str) -> str:
    contact = contact or {}
    return contact.get("remark") or contact.get("nick_name") or fallback


def discover_sources(db, *, self_wxid: str, sync_private: bool, sync_groups: bool, message_connections=None) -> tuple[list[WeChatSource], dict[str, str]]:
    contacts = load_contacts(db)
    names = {wxid: _display(contact, wxid) for wxid, contact in contacts.items()}
    names[self_wxid] = "我"
    groups: dict[str, str] = {}
    try:
        for group in db.get_groups() or []:
            wxid = str(group.get("username") or "")
            if wxid:
                groups[wxid] = str(group.get("name") or names.get(wxid) or wxid)
    except Exception:
        pass
    for wxid, contact in contacts.items():
        if wxid.endswith("@chatroom"):
            groups.setdefault(wxid, _display(contact, wxid))

    tables = _message_tables(db, message_connections)
    sources: list[WeChatSource] = []
    if sync_groups:
        for wxid, title in groups.items():
            if "Msg_" + md5_username(wxid) in tables:
                sources.append(WeChatSource(wxid, title, "group"))
    if sync_private:
        excluded = {self_wxid, "filehelper", "fmessage", "newsapp", "weixin", "medianote"}
        for wxid, contact in contacts.items():
            if wxid in excluded or wxid.endswith("@chatroom") or wxid.startswith("gh_"):
                continue
            if "Msg_" + md5_username(wxid) in tables:
                sources.append(WeChatSource(wxid, _display(contact, wxid), "private"))

    counts = Counter(source.title for source in sources)
    disambiguated = [
        WeChatSource(
            source.remote_id,
            f"{source.title} · …{source.remote_id.split('@', 1)[0][-4:]}" if counts[source.title] > 1 else source.title,
            source.conversation_type,
        )
        for source in sources
    ]
    return sorted(disambiguated, key=lambda item: (item.conversation_type, item.title.casefold())), names
