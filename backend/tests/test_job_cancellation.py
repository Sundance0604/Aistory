import json
import time

from fastapi.testclient import TestClient

import gpt_activity.api as api_module
from gpt_activity.api import create_app
from gpt_activity.config import load_settings


def _wait_for(client, status, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get("/api/jobs/status").json()
        if job["status"] == status:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {status}: {job}")


def test_topic_job_running_cancelling_cancelled_and_restart(tmp_path, monkeypatch):
    config = tmp_path / "config.local.json"
    config.write_text(
        json.dumps({"storage": {"database_path": "data/test.db", "raw_conversations_dir": "data/raw"}}),
        encoding="utf-8",
    )
    settings = load_settings(config)
    invocation = 0

    def fake_classify(_settings, **kwargs):
        nonlocal invocation
        invocation += 1
        should_cancel = kwargs["should_cancel"]
        progress = kwargs["progress_callback"]
        progress({"phase": "classifying", "total": 8, "processed": 2, "classified": 2, "failed": 0, "percent": 25})
        if invocation == 1:
            deadline = time.monotonic() + 3
            while not should_cancel() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert should_cancel()
            return {"queued": 8, "processed": 2, "classified": 2, "failed": 0, "remaining": 6, "cancelled": True}
        assert not should_cancel()
        return {"queued": 6, "processed": 6, "classified": 6, "failed": 0, "remaining": 0, "cancelled": False}

    monkeypatch.setattr(api_module, "classify_prompts", fake_classify)
    with api_module.JOB_LOCK:
        api_module.JOB_CANCEL_EVENT.clear()
        api_module.JOB.update(
            kind=None,
            status="idle",
            result=None,
            error=None,
            progress=None,
            cancel_requested=False,
        )

    client = TestClient(create_app(settings))
    started = client.post("/api/topics/classify-new", json={})
    assert started.status_code == 200
    assert started.json()["status"] == "running"

    cancelling = client.post("/api/jobs/cancel").json()
    assert cancelling["status"] == "cancelling"
    assert cancelling["cancel_requested"] is True
    repeated = client.post("/api/jobs/cancel").json()
    assert repeated["status"] == "cancelling"

    cancelled = _wait_for(client, "cancelled")
    assert cancelled["result"] == {
        "queued": 8,
        "processed": 2,
        "classified": 2,
        "failed": 0,
        "remaining": 6,
        "cancelled": True,
    }

    restarted = client.post("/api/topics/classify-new", json={})
    assert restarted.status_code == 200
    assert restarted.json()["status"] == "running"
    completed = _wait_for(client, "complete")
    assert completed["result"]["classified"] == 6
    assert invocation == 2

    idle_cancel = client.post("/api/jobs/cancel").json()
    assert idle_cancel == {"status": "idle"}
