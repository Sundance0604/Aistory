from __future__ import annotations

import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .analytics import (
    aggregate_series,
    conversation_detail,
    conversation_rankings,
    day_conversations,
    ensure_analytics,
    lifecycle,
    message_rankings,
    records,
    summary,
)
from .config import Settings, load_settings, save_settings, upsert_account
from .db import connect, ensure_account, migrate
from .importer import import_json
from .sync import last_sync_status, run_sync
from .topics import classify_prompts, topic_detail, topic_distribution, topic_timeline


JOB_LOCK = threading.Lock()
JOB: dict[str, Any] = {"kind": None, "status": "idle", "result": None, "error": None}


def _start_job(kind: str, operation: Callable[[], Any]) -> dict[str, Any]:
    with JOB_LOCK:
        if JOB["status"] == "running":
            raise HTTPException(status_code=409, detail=f"{JOB['kind']} is already running")
        JOB.update(kind=kind, status="running", result=None, error=None)

    def runner():
        try:
            result = operation()
            with JOB_LOCK:
                JOB.update(status="complete", result=result)
        except Exception as exc:
            with JOB_LOCK:
                JOB.update(status="failed", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=runner, daemon=True, name=f"gpt-activity-{kind}").start()
    return deepcopy(JOB)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    migrate(settings.database_path)
    for configured_account in settings.accounts:
        ensure_account(
            settings.database_path,
            configured_account["id"],
            configured_account.get("name") or configured_account["id"],
            configured_account.get("provider") or "chatgpt",
        )
    ensure_analytics(settings.database_path, settings.timezone, settings.values.get("analytics", {}))
    app = FastAPI(title="GPT Activity", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/summary")
    def get_summary(provider: str = "", account_id: str = ""):
        return summary(settings.database_path, settings.timezone, provider, account_id)

    @app.get("/api/activity/{granularity}")
    def get_activity(granularity: str, provider: str = "", account_id: str = ""):
        try:
            return aggregate_series(settings.database_path, settings.timezone, granularity, provider, account_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/rankings/conversations")
    def get_conversation_rankings(
        sort: str = "total_visible_tokens",
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        search: str = "",
        account_id: str = "",
        provider: str = "",
    ):
        return conversation_rankings(settings.database_path, sort, limit, offset, search, account_id, provider)

    @app.get("/api/activity/{date}/conversations")
    def get_day_conversations(date: str, provider: str = "", account_id: str = ""):
        return day_conversations(settings.database_path, date, provider, account_id)

    @app.get("/api/lifecycle")
    def get_lifecycle(provider: str = "", account_id: str = ""):
        return lifecycle(settings.database_path, provider, account_id)

    @app.get("/api/rankings/messages")
    def get_message_rankings(role: str = "user", limit: int = Query(20, ge=1, le=200), provider: str = ""):
        try:
            return message_rankings(settings.database_path, role, limit, provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/records")
    def get_records(provider: str = ""):
        return records(settings.database_path, settings.timezone, provider)

    @app.get("/api/conversations")
    def get_conversations(
        sort: str = "updated_at",
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        search: str = "",
        account_id: str = "",
        provider: str = "",
    ):
        return conversation_rankings(settings.database_path, sort, limit, offset, search, account_id, provider)

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str):
        result = conversation_detail(settings.database_path, conversation_id)
        if not result:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return result

    @app.post("/api/sync")
    def start_sync(body: dict[str, Any] | None = None):
        body = body or {}
        return _start_job(
            "sync",
            lambda: run_sync(
                settings,
                full_index=bool(body.get("full_index")),
                force_fetch=bool(body.get("force_fetch")),
                include_files=bool(body.get("include_files", False)),
                account_ids=body.get("account_ids") or None,
            ),
        )

    @app.get("/api/sync/status")
    def sync_status(account_id: str | None = None):
        with JOB_LOCK:
            current = deepcopy(JOB)
        return {"job": current, "last_sync": last_sync_status(settings, account_id)}

    @app.post("/api/import")
    def start_import(body: dict[str, Any]):
        source = Path(str(body.get("path") or "")).expanduser()
        if not source.is_absolute():
            source = (settings.root / source).resolve()
        if not source.exists():
            raise HTTPException(status_code=400, detail=f"Import path does not exist: {source}")
        account_id = str(body.get("account_id") or "default")
        try:
            account = settings.account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _start_job(
            "import",
            lambda: import_json(
                settings,
                source,
                keep_raw=bool(body.get("keep_raw", True)),
                account_id=account_id,
                account_name=account.get("name") or account_id,
                provider=account.get("provider") or "chatgpt",
            ).as_dict(),
        )

    @app.get("/api/accounts")
    def get_accounts():
        with connect(settings.database_path) as conn:
            counts = {
                (row["provider"], row["account_id"]): dict(row)
                for row in conn.execute(
                    "SELECT provider,account_id,COUNT(*) conversations FROM conversations GROUP BY provider,account_id"
                )
            }
        return [
            {**account, "conversations": counts.get((account.get("provider", "chatgpt"), account["id"]), {}).get("conversations", 0)}
            for account in settings.accounts
        ]

    @app.post("/api/accounts")
    def save_account(body: dict[str, Any]):
        try:
            saved = upsert_account(settings, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        settings.values.clear()
        settings.values.update(saved.values)
        account = saved.account(str(body.get("id")))
        ensure_account(settings.database_path, account["id"], account["name"], account.get("provider") or "chatgpt")
        return account

    @app.get("/api/data-sources")
    def get_data_sources(limit: int = Query(50, ge=1, le=500)):
        with connect(settings.database_path) as conn:
            rows = conn.execute(
                """
                SELECT ds.*,a.name account_name FROM data_sources ds
                JOIN accounts a ON a.id=ds.account_id
                ORDER BY imported_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    @app.get("/api/storage")
    def get_storage():
        return {
            "method": settings.values["storage"].get("method", "filesystem"),
            "database_path": str(settings.database_path),
            "raw_conversations_dir": str(settings.raw_dir),
            "import_roots": settings.values["storage"].get("import_roots", []),
        }

    @app.get("/api/topics")
    def get_topics(level: int = Query(1, ge=1, le=2), provider: str = "", account_id: str = ""):
        return topic_distribution(settings.database_path, level, provider, account_id)

    @app.get("/api/topics/timeline")
    def get_topic_timeline(provider: str = "", account_id: str = ""):
        return topic_timeline(settings.database_path, settings.timezone, provider, account_id)

    @app.get("/api/topics/{topic_id}")
    def get_topic(topic_id: int):
        result = topic_detail(settings.database_path, topic_id)
        if not result:
            raise HTTPException(status_code=404, detail="Topic not found")
        return result

    @app.put("/api/topics/{topic_id}/color")
    def set_topic_color(topic_id: int, body: dict[str, Any]):
        import re
        color = str(body.get("color") or "").upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", color):
            raise HTTPException(status_code=400, detail="Color must be #RRGGBB")
        with connect(settings.database_path) as conn:
            changed = conn.execute("UPDATE topics SET color=?,color_source='user' WHERE id=?", (color, topic_id)).rowcount
        if not changed:
            raise HTTPException(status_code=404, detail="Topic not found")
        return {"id": topic_id, "color": color, "color_source": "user"}

    @app.post("/api/topics/classify-new")
    def classify_new(body: dict[str, Any] | None = None):
        limit = (body or {}).get("limit")
        return _start_job("topics", lambda: classify_prompts(settings, limit=limit))

    @app.post("/api/topics/reclassify")
    def reclassify(body: dict[str, Any] | None = None):
        limit = (body or {}).get("limit")
        return _start_job("topics", lambda: classify_prompts(settings, reclassify=True, limit=limit))

    @app.get("/api/settings")
    def get_settings():
        return settings.public_values()

    @app.put("/api/settings")
    def update_settings(body: dict[str, Any]):
        allowed: dict[str, Any] = {"app": {}, "chatgpt": {}, "gemini": {}, "analytics": {}, "topics": {}, "storage": {}}
        keys = {
            "app": {"timezone"},
            "chatgpt": {"browser_channel", "include_files", "stop_on_first_unchanged"},
            "gemini": {"enabled", "secure_1psid", "secure_1psidts", "proxy", "page_size", "read_limit", "recent_refetch_count", "retry_delays_seconds"},
            "analytics": {"session_gap_minutes", "single_prompt_minutes", "session_tail_minutes"},
            "topics": {"provider", "base_url", "model", "api_key", "preferences"},
            "storage": {"method", "database_path", "raw_conversations_dir", "import_roots"},
        }
        for section, section_keys in keys.items():
            for key in section_keys:
                if key in (body.get(section) or {}):
                    value = body[section][key]
                    if key in {"api_key", "secure_1psid", "secure_1psidts"} and not value:
                        continue
                    allowed[section][key] = value
        saved = save_settings(settings, allowed)
        settings.values.clear()
        settings.values.update(saved.values)
        migrate(settings.database_path)
        for configured_account in settings.accounts:
            ensure_account(
                settings.database_path,
                configured_account["id"],
                configured_account.get("name") or configured_account["id"],
                configured_account.get("provider") or "chatgpt",
            )
        ensure_analytics(settings.database_path, settings.timezone, settings.values.get("analytics", {}))
        return settings.public_values()

    frontend = settings.root / "frontend" / "dist"
    if frontend.exists():
        assets = frontend / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}")
        def frontend_app(path: str):
            requested = frontend / path
            if path and requested.is_file() and frontend in requested.resolve().parents:
                return FileResponse(requested)
            return FileResponse(frontend / "index.html")

    return app
