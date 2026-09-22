from datetime import datetime, timedelta, timezone
from pathlib import Path

from gpt_activity.db import connect, ensure_account, migrate
from gpt_activity.usage_time import (
    boundary_decisions,
    boundary_diagnostics,
    build_sessions,
    refresh_usage_time,
    semantic_state_order,
    session_duration_diagnostics,
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
    assert result["gmm"]["selected_k_bic"] in {1, 2, 3, 4, 5, 6}
    assert result["hmm"]["states"] == 2
    assert result["gmm"]["estimated_usage_seconds"] > 0
    assert result["hmm"]["estimated_usage_seconds"] > 0
    assert len([row for row in usage_candidates(database) if row["model_family"] == "gmm"]) == 6
    assert usage_sessions(database, "gmm")
    assert usage_sessions(database, "hmm")
    assert usage_disagreements(database)
    summary = usage_summary(database)
    assert summary["model_version"] == "usage-time-v3-gap-regimes"
    assert summary["primary_features"] == ["log1p_gap"]
    assert summary["confusion_matrix"]
    assert all(row["feature_set"] == "gap only" for row in usage_candidates(database) if row["model_family"] in {"gmm", "hmm"})


def test_raw_usage_statistics_survive_insufficient_samples(tmp_path):
    database = tmp_path / "small.db"
    seed_history(database, event_count=3)
    result = refresh_usage_time(database, {"min_model_samples": 50})
    assert result["status"] == "insufficient"
    assert result["sample_count"] == 2
    assert len(result["distribution"]["histogram"]) > 0
    assert result["gmm"] is None


def test_stable_state_order_uses_gap_characteristic_not_raw_id():
    assert semantic_state_order([3600, 120]) == [1, 0]


def test_boundary_probability_is_separate_from_state_identity():
    probabilities = [0.49, 0.50, 0.90]
    assert boundary_decisions(probabilities, 0.5).tolist() == [False, True, True]
    assert boundary_decisions(probabilities, 0.8).tolist() == [False, False, True]


def test_explicit_gap_local_session_segmentation():
    base = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    minutes = [0, 3, 7, 180, 184]
    events = [{
        "timestamp": (base + timedelta(minutes=minute)).isoformat(),
        "epoch": (base + timedelta(minutes=minute)).timestamp(),
        "input_tokens": 10,
        "output_tokens": 20,
    } for minute in minutes]
    sessions = build_sessions(events, [False, False, True, False], 300)
    assert [row["event_count"] for row in sessions] == [3, 2]
    assert [row["session_span_seconds"] for row in sessions] == [420, 240]
    assert sessions[0]["max_internal_gap_seconds"] == 240
    assert sessions[0]["estimated_usage_seconds"] == 720


def test_anomaly_and_boundary_diagnostics_are_explicit():
    sessions = [
        {"estimated_usage_seconds": 300},
        {"estimated_usage_seconds": 7 * 3600},
        {"estimated_usage_seconds": 25 * 3600},
    ]
    diagnostics = session_duration_diagnostics(sessions)
    assert diagnostics["over_6h"] == 2
    assert diagnostics["over_12h"] == 1
    assert diagnostics["over_24h"] == 1
    gaps = [{"gap_seconds": value} for value in [60, 120, 3600, 180]]
    boundaries = boundary_diagnostics(gaps, [False, False, True, False])
    assert boundaries["boundaries"] == 1
    assert boundaries["sessions"] == 2
    assert boundaries["minimum_break_gap_seconds"] == 3600


def test_ui_uses_gap_regime_transition_terms():
    source = (Path(__file__).parents[2] / "frontend" / "src" / "pages" / "UsageTime.tsx").read_text(encoding="utf-8")
    assert "Short-gap → Short-gap" in source
    assert "Long-gap → Long-gap" in source
    assert "Active →" not in source
    assert "Inactive →" not in source
