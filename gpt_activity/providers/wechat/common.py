"""Stable helpers preserved from EasyInternship's verified reader."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime


def md5_username(username: str) -> str:
    return hashlib.md5(username.encode("utf-8")).hexdigest()


def stable_id(*parts: object) -> str:
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def format_time(value: object) -> str:
    try:
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp /= 1000
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "" if value is None else str(value)


def normalize_sender_id(value: object) -> str:
    return "" if value is None else str(value)


def table_exists(conn, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)
    ).fetchone() is not None
