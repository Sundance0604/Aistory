import json

from gpt_activity.config import load_settings
from gpt_activity.sync import ChatGPTWebSource, IndexItem, plan_sync, run_sync


def item(identifier, update):
    return IndexItem(identifier, identifier, None, update, f"https://chatgpt.com/c/{identifier}")


def test_incremental_plan_fetches_only_new_and_changed():
    local = {
        "A": {"updated_at": "2024-01-01T00:00:00+00:00", "content_hash": "a"},
        "B": {"updated_at": "2024-01-01T00:00:00+00:00", "content_hash": "b"},
        "C": {"updated_at": "2024-01-01T00:00:00+00:00", "content_hash": "c"},
    }
    remote = [
        item("A", "2024-01-01T00:00:00Z"),
        item("B", "2024-01-02T00:00:00Z"),
        item("C", "2024-01-01T00:00:00Z"),
        item("D", "2024-01-01T00:00:00Z"),
    ]
    plan = plan_sync(local, remote)
    assert [entry.id for entry in plan.new] == ["D"]
    assert [entry.id for entry in plan.changed] == ["B"]
    assert [entry.id for entry in plan.unchanged] == ["A", "C"]


def test_second_plan_is_idempotent():
    remote = [item("A", "2024-01-01T00:00:00Z")]
    local = {"A": {"updated_at": "2024-01-01T00:00:00+00:00", "content_hash": "hash"}}
    plan = plan_sync(local, remote)
    assert not plan.new and not plan.changed and len(plan.unchanged) == 1


def test_index_stops_inside_first_page_at_first_unchanged(tmp_path):
    config = tmp_path / "config.local.json"
    config.write_text("{}", encoding="utf-8")
    source = ChatGPTWebSource(load_settings(config))
    calls = []

    def fake_get(url):
        calls.append(url)
        if "/backend-api/conversations?" in url:
            return {
                "total": 400,
                "items": [
                    {"id": "new", "update_time": "2024-01-03T00:00:00Z"},
                    {"id": "changed", "update_time": "2024-01-02T00:00:00Z"},
                    {"id": "same", "update_time": "2024-01-01T00:00:00Z"},
                    {"id": "older", "update_time": "2023-12-01T00:00:00Z"},
                ],
            }
        return {"items": []}

    source._get = fake_get
    local = {
        "changed": {"updated_at": "2024-01-01T00:00:00+00:00", "content_hash": "x"},
        "same": {"updated_at": "2024-01-01T00:00:00+00:00", "content_hash": "y"},
    }
    result = source.list_conversations(local_rows=local, stop_on_unchanged=True)
    assert [item.id for item in result] == ["new", "changed", "same"]
    assert sum("/backend-api/conversations?" in call for call in calls) == 1


def test_multiple_accounts_are_synchronized_sequentially(tmp_path, monkeypatch):
    config = tmp_path / "config.local.json"
    config.write_text(
        json.dumps(
            {
                "storage": {"database_path": "data/test.db", "raw_conversations_dir": "data/raw"},
                "accounts": [
                    {"id": "one", "name": "One", "browser_profile": "profiles/one", "enabled": True},
                    {"id": "two", "name": "Two", "browser_profile": "profiles/two", "enabled": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(config)
    order = []

    def fake_sync(_settings, account, **_kwargs):
        order.append(account["id"])
        return {
            "account_id": account["id"], "account_name": account["name"], "status": "complete",
            "index_items_seen": 0, "new_conversations": 0, "updated_conversations": 0,
            "unchanged_conversations": 0, "fetched_conversations": 0,
            "failed_conversations": 0, "new_messages": 0, "error": None,
        }

    monkeypatch.setattr("gpt_activity.sync._sync_account", fake_sync)
    result = run_sync(settings)
    assert order == ["one", "two"]
    assert [item["account_id"] for item in result["accounts"]] == order
