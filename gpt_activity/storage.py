from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from .config import Settings


def safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "unknown"


class RawConversationStorage(Protocol):
    method: str

    def write(self, account_id: str, conversation_id: str, data: dict) -> Path: ...


class FileSystemRawStorage:
    method = "filesystem"

    def __init__(self, root: Path):
        self.root = root

    def write(self, account_id: str, conversation_id: str, data: dict) -> Path:
        directory = self.root / safe_segment(account_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{safe_segment(conversation_id)}.json"
        handle, temp_name = tempfile.mkstemp(
            prefix=f".{safe_segment(conversation_id)}.", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return target


def raw_storage(settings: Settings) -> RawConversationStorage:
    method = settings.values["storage"].get("method", "filesystem")
    if method == "filesystem":
        return FileSystemRawStorage(settings.raw_dir)
    raise ValueError(f"Unsupported storage method: {method}")
