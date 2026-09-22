import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from gpt_activity.config import load_settings
from gpt_activity.db import connect
from gpt_activity.importer import import_json
from gpt_activity.topics import TopicClassificationResult, classify_prompts


FIXTURE = Path(__file__).parent / "fixtures" / "branching_conversation.json"


def _settings(tmp_path, concurrency=1):
    config = tmp_path / "config.local.json"
    config.write_text(
        json.dumps({
            "storage": {"database_path": "data/test.db", "raw_conversations_dir": "data/raw"},
            "topics": {"max_concurrency": concurrency},
        }),
        encoding="utf-8",
    )
    settings = load_settings(config)
    import_json(settings, FIXTURE)
    return settings


def _add_prompts(settings, count):
    with connect(settings.database_path) as conn:
        for index in range(count):
            message_id = f"extra-user-{index}"
            conn.execute(
                """
                INSERT INTO messages(
                    id,conversation_id,parent_id,role,created_at,model,content_type,
                    visible_text,visible_tokens,tokenizer_version,content_hash,
                    sequence_index,is_active_branch,has_attachment,analyzable,raw_metadata_json
                ) VALUES(?, 'conv-1', NULL, 'user', ?, NULL, 'text', ?, 3, 'test', ?, ?, 1, 0, 1, NULL)
                """,
                (
                    message_id,
                    datetime.now(timezone.utc).isoformat(),
                    f"Prompt {index}",
                    f"hash-{index}",
                    10 + index,
                ),
            )


def _result(topic="New topic"):
    return TopicClassificationResult.model_validate({
        "topics": [{"parent_topic": "Testing", "topic": topic, "weight": 1}],
        "confidence": 1,
    })


def test_cancellation_stops_new_calls_and_keeps_completed_result(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _add_prompts(settings, 4)
    started = threading.Event()
    release = threading.Event()
    cancel = threading.Event()
    calls = 0

    class BlockingClassifier:
        classifier_name = "fake"
        classifier_version = "fake:v1"

        def __init__(self, _settings):
            pass

        def classify(self, _request, should_cancel=None):
            nonlocal calls
            calls += 1
            started.set()
            assert release.wait(3)
            return _result()

        def close(self):
            pass

    monkeypatch.setattr("gpt_activity.topics.DeepSeekTopicClassifier", BlockingClassifier)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(classify_prompts, settings, should_cancel=cancel.is_set)
        assert started.wait(3)
        cancel.set()
        release.set()
        result = future.result(timeout=5)

    assert result["cancelled"] is True
    assert result["processed"] == result["classified"] == 1
    assert result["processed"] < result["queued"]
    assert result["remaining"] == result["queued"] - 1
    assert calls == 1
    with connect(settings.database_path) as conn:
        saved = conn.execute(
            "SELECT COUNT(*) count FROM message_topics WHERE classifier_version='fake:v1'"
        ).fetchone()["count"]
    assert saved == 1


def test_cancelled_reclassify_preserves_unprocessed_old_topics(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _add_prompts(settings, 3)
    with connect(settings.database_path) as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO topics(parent_id,name,slug,level,color,color_source,created_at) "
            "VALUES(NULL,'Old','old',1,'#000000','auto',?)",
            (now,),
        )
        topic_id = conn.execute("SELECT id FROM topics WHERE slug='old'").fetchone()["id"]
        prompt_ids = [row["id"] for row in conn.execute(
            "SELECT id FROM messages WHERE role='user' AND is_active_branch=1 AND analyzable=1 AND trim(visible_text)<>''"
        )]
        for message_id in prompt_ids:
            conn.execute(
                "INSERT INTO message_topics(message_id,topic_id,weight,classifier,classifier_version,classified_at) "
                "VALUES(?,?,1,'old','old:v1',?)",
                (message_id, topic_id, now),
            )

    started = threading.Event()
    release = threading.Event()
    cancel = threading.Event()

    class BlockingClassifier:
        classifier_name = "fake"
        classifier_version = "fake:v2"

        def __init__(self, _settings):
            pass

        def classify(self, _request, should_cancel=None):
            started.set()
            assert release.wait(3)
            return _result("Replacement")

        def close(self):
            pass

    monkeypatch.setattr("gpt_activity.topics.DeepSeekTopicClassifier", BlockingClassifier)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            classify_prompts,
            settings,
            reclassify=True,
            should_cancel=cancel.is_set,
        )
        assert started.wait(3)
        cancel.set()
        release.set()
        result = future.result(timeout=5)

    assert result["classified"] == 1
    with connect(settings.database_path) as conn:
        prompts_without_topics = conn.execute(
            """
            SELECT COUNT(*) count FROM messages m
            WHERE m.role='user' AND m.is_active_branch=1 AND m.analyzable=1 AND trim(m.visible_text)<>''
              AND NOT EXISTS (SELECT 1 FROM message_topics mt WHERE mt.message_id=m.id)
            """
        ).fetchone()["count"]
        old_rows = conn.execute(
            "SELECT COUNT(*) count FROM message_topics WHERE classifier_version='old:v1'"
        ).fetchone()["count"]
    assert prompts_without_topics == 0
    assert old_rows == len(prompt_ids) - 1


def test_topic_concurrency_is_capped_at_ten(tmp_path, monkeypatch):
    settings = _settings(tmp_path, concurrency=99)
    _add_prompts(settings, 11)
    release = threading.Event()
    ten_started = threading.Event()
    state_lock = threading.Lock()
    active = peak = calls = 0

    class CountingClassifier:
        classifier_name = "fake"
        classifier_version = "fake:v3"

        def __init__(self, _settings):
            pass

        def classify(self, _request, should_cancel=None):
            nonlocal active, peak, calls
            with state_lock:
                active += 1
                calls += 1
                peak = max(peak, active)
                if active == 10:
                    ten_started.set()
            assert release.wait(3)
            with state_lock:
                active -= 1
            return _result("Concurrent")

        def close(self):
            pass

    monkeypatch.setattr("gpt_activity.topics.DeepSeekTopicClassifier", CountingClassifier)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(classify_prompts, settings)
        assert ten_started.wait(3)
        release.set()
        result = future.result(timeout=8)

    assert peak == 10
    assert calls == result["queued"]
    assert result["classified"] == result["queued"]
