import json
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient

from gpt_activity.analytics import conversation_rankings, day_conversations, lifecycle, summary, daily_series
from gpt_activity.api import create_app
from gpt_activity.config import load_settings
from gpt_activity.db import connect
from gpt_activity.importer import import_json


FIXTURE = Path(__file__).parent / "fixtures" / "branching_conversation.json"


def settings_for(tmp_path, timezone="UTC"):
    config = tmp_path / "config.local.json"
    config.write_text(
        json.dumps(
            {
                "app": {"timezone": timezone},
                "storage": {
                    "database_path": "data/test.db",
                    "raw_conversations_dir": "data/raw"
                }
            }
        ),
        encoding="utf-8",
    )
    return load_settings(config)


def test_import_is_idempotent(tmp_path):
    settings = settings_for(tmp_path)
    first = import_json(settings, FIXTURE)
    second = import_json(settings, FIXTURE)
    assert first.imported == 1
    assert second.unchanged == 1
    with connect(settings.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) n FROM conversations").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"] == 8


def test_metrics_count_only_active_visible_turns(tmp_path):
    settings = settings_for(tmp_path)
    import_json(settings, FIXTURE)
    result = summary(settings.database_path)
    assert result["conversations"] == 1
    assert result["prompts"] == 2
    assert result["prompt_visible_tokens"] > 0
    assert result["response_visible_tokens"] > 0


def test_timezone_moves_activity_across_day_boundary(tmp_path):
    settings = settings_for(tmp_path, "Asia/Shanghai")
    import_json(settings, FIXTURE)
    days = daily_series(settings.database_path, settings.timezone)
    assert any(item["date"] == "2024-01-01" and item["prompts"] == 2 for item in days)


def test_materialized_lifecycle_and_day_drilldown(tmp_path):
    settings = settings_for(tmp_path, "Asia/Shanghai")
    import_json(settings, FIXTURE)
    life = lifecycle(settings.database_path)
    assert life[0]["prompts"] == 2
    assert life[0]["active_days"] == 1
    assert life[0]["session_count"] >= 1
    drilldown = day_conversations(settings.database_path, "2024-01-01")
    assert len(drilldown) == 1
    assert drilldown[0]["prompts"] == 2
    assert drilldown[0]["total_visible_tokens"] > 0


def test_conversations_can_be_filtered_by_parent_topic(tmp_path):
    settings = settings_for(tmp_path)
    import_json(settings, FIXTURE)
    with connect(settings.database_path) as conn:
        conversation_id = conn.execute("SELECT id FROM conversations").fetchone()["id"]
        message_id = conn.execute("SELECT id FROM messages WHERE role='user' LIMIT 1").fetchone()["id"]
        parent_id = conn.execute(
            "INSERT INTO topics(name,slug,level,color,color_source,created_at) VALUES('Programming','programming',1,'#123456','auto','now')"
        ).lastrowid
        child_id = conn.execute(
            "INSERT INTO topics(parent_id,name,slug,level,color,color_source,created_at) VALUES(?, 'Python','programming/python',2,'#234567','auto','now')",
            (parent_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO message_topics(message_id,topic_id,weight,classifier,classifier_version,classified_at) VALUES(?,?,1,'test','test','now')",
            (message_id, child_id),
        )
    assert [row["id"] for row in conversation_rankings(settings.database_path, topic_id=parent_id)] == [conversation_id]
    assert conversation_rankings(settings.database_path, topic_id=999999) == []


def test_conversation_filters_accept_blank_topic_and_search_content(tmp_path):
    settings = settings_for(tmp_path)
    import_json(settings, FIXTURE)
    client = TestClient(create_app(settings))
    response = client.get(
        "/api/conversations",
        params={"topic_id": "", "account_id": "default", "search": "Cayley table"},
    )
    assert response.status_code == 200
    assert [row["title"] for row in response.json()] == ["Branch fixture"]
    assert client.get("/api/conversations", params={"topic_id": "", "account_id": "missing"}).json() == []


def test_official_export_zip_and_same_remote_id_are_namespaced_by_account(tmp_path):
    settings = settings_for(tmp_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    export_zip = tmp_path / "chatgpt-export.zip"
    with ZipFile(export_zip, "w") as archive:
        archive.writestr("conversations.json", json.dumps([payload]))

    import_json(settings, FIXTURE, account_id="default", account_name="Personal")
    result = import_json(settings, export_zip, account_id="work", account_name="Work")
    assert result.imported == 1
    with connect(settings.database_path) as conn:
        rows = conn.execute(
            "SELECT id,account_id,remote_id,raw_json_path FROM conversations ORDER BY account_id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["remote_id"] == rows[1]["remote_id"]
        assert rows[0]["id"] != rows[1]["id"]
        assert {row["account_id"] for row in rows} == {"default", "work"}
        assert conn.execute("SELECT COUNT(*) n FROM data_sources").fetchone()["n"] == 2
