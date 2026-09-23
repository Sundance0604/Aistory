from __future__ import annotations

from typing import Any


PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "chatgpt": {"label": "ChatGPT", "supports_topics": True, "supports_usage_time": True},
    "gemini": {"label": "Gemini", "supports_topics": True, "supports_usage_time": True},
    "wechat": {"label": "WeChat", "supports_topics": False, "supports_usage_time": True},
}


def providers() -> list[dict[str, Any]]:
    return [{"id": provider_id, **capabilities} for provider_id, capabilities in PROVIDER_CAPABILITIES.items()]


def topic_provider_ids() -> tuple[str, ...]:
    return tuple(
        provider_id
        for provider_id, capabilities in PROVIDER_CAPABILITIES.items()
        if capabilities.get("supports_topics")
    )
