import json
from pathlib import Path

from gpt_activity.parser import active_branch_ids, normalize_conversation, visible_text


FIXTURE = Path(__file__).parent / "fixtures" / "branching_conversation.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_active_branch_excludes_regenerated_answer():
    data = load_fixture()
    branch = active_branch_ids(data)
    assert "a-current" in branch
    assert "a-old" not in branch
    assert branch[-1] == "a2"


def test_visible_messages_and_attachment_only_prompt():
    normalized = normalize_conversation(load_fixture())
    active = [message for message in normalized.messages if message.is_active_branch]
    assert sum(message.role == "user" for message in active) == 2
    assert sum(message.role == "assistant" and bool(message.visible_text) for message in active) == 2
    attachment = next(message for message in active if message.id == "u2-msg")
    assert attachment.has_attachment is True
    assert attachment.visible_text == ""
    assert attachment.visible_tokens == 0
    assert next(message for message in normalized.messages if message.id == "tool-msg").visible_text == ""
    assert next(message for message in normalized.messages if message.id == "reasoning-msg").visible_text == ""


def test_fallback_branch_is_deterministic():
    data = load_fixture()
    data.pop("current_node")
    first = active_branch_ids(data)
    second = active_branch_ids(data)
    assert first == second
    assert first[-1] in {"a-old", "a2", "reasoning"}
