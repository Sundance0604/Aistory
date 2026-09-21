from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.local.json"

DEFAULTS: dict[str, Any] = {
    "app": {"host": "127.0.0.1", "port": 8765, "timezone": "Asia/Shanghai"},
    "storage": {
        "method": "filesystem",
        "database_path": "data/gpt_activity.db",
        "raw_conversations_dir": "data/raw/conversations",
        "import_roots": [],
    },
    "accounts": [
        {
            "id": "default",
            "name": "默认账号",
            "browser_profile": "browser_profile",
            "enabled": True,
        }
    ],
    "chatgpt": {
        "base_url": "https://chatgpt.com",
        "browser_profile": "browser_profile",
        "browser_channel": "chrome",
        "include_files": False,
        "request_timeout_seconds": 45,
        "min_delay_seconds": 10,
        "max_delay_seconds": 16,
        "stop_on_first_unchanged": True,
    },
    "topics": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "",
        "classifier_schema_version": "topic-schema-v1",
        "max_context_chars": 2400,
        "max_concurrency": 4,
        "request_timeout_seconds": 90,
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


@dataclass(frozen=True)
class Settings:
    path: Path
    values: dict[str, Any]

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def database_path(self) -> Path:
        return _resolve(self.root, self.values["storage"]["database_path"])

    @property
    def raw_dir(self) -> Path:
        return _resolve(self.root, self.values["storage"]["raw_conversations_dir"])

    @property
    def browser_profile(self) -> Path:
        return _resolve(self.root, self.values["chatgpt"]["browser_profile"])

    @property
    def accounts(self) -> list[dict[str, Any]]:
        accounts = self.values.get("accounts") or []
        return [dict(account) for account in accounts if account.get("id")]

    def account(self, account_id: str) -> dict[str, Any]:
        for account in self.accounts:
            if account["id"] == account_id:
                return account
        raise KeyError(f"Unknown account: {account_id}")

    def browser_profile_for(self, account: dict[str, Any]) -> Path:
        value = account.get("browser_profile") or self.values["chatgpt"]["browser_profile"]
        return _resolve(self.root, value)

    @property
    def timezone(self) -> str:
        name = self.values["app"]["timezone"]
        ZoneInfo(name)
        return name

    @property
    def api_key(self) -> str:
        return os.environ.get("GPT_ACTIVITY_API_KEY") or self.values["topics"].get("api_key", "")

    def public_values(self) -> dict[str, Any]:
        public = deepcopy(self.values)
        public["topics"]["api_key"] = ""
        public["topics"]["api_key_configured"] = bool(self.api_key)
        return public


def load_settings(path: str | Path | None = None) -> Settings:
    config_path = Path(path or os.environ.get("GPT_ACTIVITY_CONFIG", DEFAULT_CONFIG_PATH)).resolve()
    supplied: dict[str, Any] = {}
    if config_path.exists():
        supplied = json.loads(config_path.read_text(encoding="utf-8"))
    values = _merge(DEFAULTS, supplied)
    if not values.get("accounts"):
        values["accounts"] = deepcopy(DEFAULTS["accounts"])
    settings = Settings(config_path, values)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    return settings


def save_settings(settings: Settings, updates: dict[str, Any]) -> Settings:
    values = _merge(settings.values, updates)
    settings.path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return load_settings(settings.path)


def upsert_account(settings: Settings, account: dict[str, Any]) -> Settings:
    account_id = str(account.get("id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", account_id):
        raise ValueError("Account id must use 1-64 letters, numbers, dot, dash, or underscore")
    name = str(account.get("name") or account_id).strip()
    profile = str(account.get("browser_profile") or f"browser_profiles/{account_id}").strip()
    entry = {
        "id": account_id,
        "name": name,
        "browser_profile": profile,
        "enabled": bool(account.get("enabled", True)),
    }
    accounts = settings.accounts
    replaced = False
    for index, existing in enumerate(accounts):
        if existing["id"] == account_id:
            accounts[index] = entry
            replaced = True
            break
    if not replaced:
        accounts.append(entry)
    return save_settings(settings, {"accounts": accounts})
