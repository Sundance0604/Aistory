from datetime import datetime, timedelta, timezone

from gpt_activity.db import connect, ensure_account, migrate
from gpt_activity.usage_time import (
    refresh_usage_time,
    usage_candidates,
    usage_disagreements,
    usage_sessions,
    usage_summary,
)


def seed_history(path, event_count=61):
    migrate(path)
    ensure_account(path, "default", "Default", "chatgpt")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO conversations(
            id,account_id,provider,remote_id,title,created_at,updated_at,fetched_at,content_hash
            ) VALUES('conversation','default','chatgpt','conversation','History',?,?,?,?)""",
            (now.isoformat(), (now + timedelta(days=1)).isoformat(), now.isoformat(), "hash"),
        )
        stamp = now
        for index in range(event_count):
            if index and index % 10 == 0:
                stamp += timedelta(hours=3)
            elif index:
                stamp += timedelta(minutes=2 + index % 3)
            for role, offset, tokens in (("user", 0, 20 + index), ("assistant", 1, 80 + index * 2)):
                message_id = f"{role}-{index}"
                conn.execute(
                    """INSERT INTO messages(
                    id,conversation_id,role,created_at,visible_text,visible_tokens,tokenizer_version,
                    content_hash,sequence_index,is_active_branch,has_attachment,analyzable
                    ) VALUES(?,?,?,?,?,?,?,?,?,1,0,1)""",
                    (message_id, "conversation", role, (stamp + timedelta(seconds=offset)).isoformat(), role, tokens, "test", message_id, index * 2 + offset),
                )


def test_global_usage_time_models_are_persisted(tmp_path):
    database = tmp_path / "usage.db"
    seed_history(database)
    result = refresh_usage_time(database, {"tail_allowance_minutes": 5, "min_model_samples": 10, "random_state": 7})
    assert result["status"] == "complete"
    assert result["user_events"] == 61
    assert result["sample_count"] == 60
    assert result["gmm"]["selected_k_bic"] in {1, 2, 3, 4}
    assert result["hmm"]["states"] == 2
    assert result["gmm"]["estimated_usage_seconds"] > 0
    assert result["hmm"]["estimated_usage_seconds"] > 0
    assert len([row for row in usage_candidates(database) if row["model_family"] == "gmm"]) == 4
    assert usage_sessions(database, "gmm")
    assert usage_sessions(database, "hmm")
    assert usage_disagreements(database)
    assert usage_summary(database)["model_version"] == "usage-time-v2"


def test_raw_usage_statistics_survive_insufficient_samples(tmp_path):
    database = tmp_path / "small.db"
    seed_history(database, event_count=3)
    result = refresh_usage_time(database, {"min_model_samples": 50})
    assert result["status"] == "insufficient"
    assert result["sample_count"] == 2
    assert len(result["distribution"]["histogram"]) > 0
    assert result["gmm"] is None
