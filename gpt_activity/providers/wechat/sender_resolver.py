"""Sender inference preserved from EasyInternship's verified group collector."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .common import md5_username, normalize_sender_id, table_exists


FROM_PATTERNS = [
    re.compile(r'fromusername\s*=\s*"([^"]+)"', re.I),
    re.compile(r"fromusername\s*=\s*'([^']+)'", re.I),
    re.compile(r"<fromusername><!\[CDATA\[(.*?)\]\]></fromusername>", re.I | re.S),
    re.compile(r"<fromusername>(.*?)</fromusername>", re.I | re.S),
]


def valid_sender_candidate(candidate, known_ids: set[str], conversation_wxid: str) -> bool:
    if not candidate:
        return False
    candidate = candidate.strip()
    return candidate != conversation_wxid and (candidate in known_ids or candidate.startswith("wxid_"))


def sender_prefix(text: str, known_ids: set[str], conversation_wxid: str):
    if not text:
        return None, 0
    match = re.match(r"^([^:\r\n]{1,160}):\s*", text.lstrip("\ufeff"))
    if match and valid_sender_candidate(match.group(1), known_ids, conversation_wxid):
        return match.group(1).strip(), match.end()
    return None, 0


def xml_sender(text: str, known_ids: set[str], conversation_wxid: str):
    if not text:
        return None
    for pattern in FROM_PATTERNS:
        match = pattern.search(text)
        if match and valid_sender_candidate(match.group(1), known_ids, conversation_wxid):
            return match.group(1).strip()
    return None


def explicit_sender_from_row(row: dict, known_ids: set[str], conversation_wxid: str):
    from .message_parser import decode_payload
    decoded = {field: decode_payload(row.get(field)) for field in ("message_content", "compress_content", "source", "packed_info_data")}
    sender, _ = sender_prefix(decoded["message_content"], known_ids, conversation_wxid)
    if sender:
        return sender
    for value in decoded.values():
        sender = xml_sender(value, known_ids, conversation_wxid)
        if sender:
            return sender
    return None


def filehelper_self_ids(conn) -> set[str]:
    table = "Msg_" + md5_username("filehelper")
    if not table_exists(conn, table):
        return set()
    try:
        rows = conn.execute(f'SELECT DISTINCT real_sender_id FROM "{table}" WHERE real_sender_id IS NOT NULL').fetchall()
    except Exception:
        return set()
    return {normalize_sender_id(row[0]) for row in rows if normalize_sender_id(row[0]) not in {"", "0", "None"}}


def sender_votes(rows: list[dict], known_ids: set[str], conversation_wxid: str):
    votes: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        sender_id = normalize_sender_id(row.get("real_sender_id"))
        if sender_id in {"", "0", "None"}:
            continue
        sender = explicit_sender_from_row(row, known_ids, conversation_wxid)
        if sender:
            votes[sender_id][sender] += 1
    return votes
