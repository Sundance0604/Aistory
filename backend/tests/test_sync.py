import json

from gpt_activity.config import load_settings
from gpt_activity.sync import ChatGPTWebSource, IndexItem, ordered_fetch_queue, plan_sync, run_sync


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


def test_fetch_queue_preserves_remote_newest_first_order():
    remote = [
        item("changed-newest", "2024-01-04T00:00:00Z"),
        item("new-middle", "2024-01-03T00:00:00Z"),
        item("changed-older", "2024-01-02T00:00:00Z"),
    ]
    local = {
        "changed-newest": {"updated_at": "2024-01-01T00:00:00Z", "content_hash": "a"},
        "changed-older": {"updated_at": "2024-01-01T00:00:00Z", "content_hash": "b"},
    }
    plan = plan_sync(local, remote)
    assert [entry.id for entry in plan.new] == ["new-middle"]
    assert [entry.id for entry in plan.changed] == ["changed-newest", "changed-older"]
    assert [entry.id for entry in ordered_fetch_queue(remote, plan)] == [
        "changed-newest", "new-middle", "changed-older",
    ]


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


def test_detail_unchanged_stops_older_fetches_but_not_other_projects(tmp_path, monkeypatch):
    config = tmp_path / "config.local.json"
    config.write_text(json.dumps({
        "storage": {"database_path": "data/test.db", "raw_conversations_dir": "data/raw"},
        "chatgpt": {"min_delay_seconds": 0, "max_delay_seconds": 0},
        "accounts": [{"id": "one", "name": "One", "browser_profile": "profile", "enabled": True}],
    }), encoding="utf-8")
    settings = load_settings(config)
    fetched = []

    remote = [
        item("new-main", "2024-01-05T00:00:00Z"),
        item("same-content", "2024-01-04T00:00:00Z"),
        item("older-main", "2024-01-03T00:00:00Z"),
        IndexItem("project-new", "project-new", None, "2024-01-05T00:00:00Z", "url", "project-1"),
    ]
    local = {
        "same-content": {"updated_at": "2024-01-01T00:00:00Z", "content_hash": "same"},
        "older-main": {"updated_at": "2024-01-01T00:00:00Z", "content_hash": "older"},
    }

    class FakeSource:
        def __init__(self, _settings, _account):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_conversations(self, **_kwargs):
            return remote

        def fetch_conversation(self, entry):
            fetched.append(entry.id)
            return {"id": entry.id}

        def include_files(self):
            raise AssertionError("files should not be requested")

    def fake_upsert(_settings, data, **_kwargs):
        states = {
            "new-main": "new",
            "same-content": "unchanged",
            "older-main": "updated",
            "project-new": "new",
        }
        return states[data["id"]], 0

    monkeypatch.setattr("gpt_activity.sync.ChatGPTWebSource", FakeSource)
    monkeypatch.setattr("gpt_activity.sync._local_index", lambda *_args: local)
    monkeypatch.setattr("gpt_activity.sync.upsert_conversation", fake_upsert)

    result = run_sync(settings)

    assert fetched == ["new-main", "same-content", "project-new"]
    assert "older-main" not in fetched
    assert result["fetched_conversations"] == 3


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


def test_provider_sync_status_is_persisted_per_account(tmp_path, monkeypatch):
    config = tmp_path / "config.local.json"
    config.write_text(json.dumps({
        "storage": {"database_path": "data/test.db", "raw_conversations_dir": "data/raw"},
        "accounts": [{"id": "gem", "name": "Gem", "provider": "gemini", "enabled": True}],
    }), encoding="utf-8")
    settings = load_settings(config)

    def fail_sync(*_args, **_kwargs):
        raise RuntimeError("authentication expired")

    monkeypatch.setattr("gpt_activity.gemini.run_gemini_sync", fail_sync)
    result = run_sync(settings)
    assert result["status"] == "failed"
    from gpt_activity.db import connect
    with connect(settings.database_path) as conn:
        saved = json.loads(conn.execute("SELECT value FROM app_metadata WHERE key='last_sync_status:gem'").fetchone()["value"])
    assert saved["status"] == "failed"
    assert "authentication expired" in saved["error"]
