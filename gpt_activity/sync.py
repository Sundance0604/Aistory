from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .db import connect, ensure_account, migrate
from .importer import upsert_conversation
from .parser import iso_utc


@dataclass(frozen=True)
class IndexItem:
    id: str
    title: str
    create_time: str | None
    update_time: str | None
    url: str
    project_id: str | None = None


@dataclass
class SyncPlan:
    new: list[IndexItem]
    changed: list[IndexItem]
    unchanged: list[IndexItem]


def plan_sync(local_rows: dict[str, dict[str, Any]], remote: list[IndexItem], force_fetch: bool = False) -> SyncPlan:
    new: list[IndexItem] = []
    changed: list[IndexItem] = []
    unchanged: list[IndexItem] = []
    for item in remote:
        local = local_rows.get(item.id)
        if not local:
            new.append(item)
        elif force_fetch or not local.get("content_hash"):
            changed.append(item)
        elif item.update_time and iso_utc(item.update_time) != iso_utc(local.get("updated_at")):
            changed.append(item)
        else:
            unchanged.append(item)
    return SyncPlan(new, changed, unchanged)


def ordered_fetch_queue(remote: list[IndexItem], plan: SyncPlan) -> list[IndexItem]:
    """Keep the provider's newest-first order instead of grouping by action."""
    fetch_ids = {item.id for item in (*plan.new, *plan.changed)}
    return [item for item in remote if item.id in fetch_ids]


def _index_stream(item: IndexItem) -> str:
    # The main conversation list and every project are independently ordered
    # newest-first. An unchanged item in one stream must not hide another
    # project's newer conversations.
    return item.project_id or "__main__"


class ChatGPTWebSource:
    """Small replaceable adapter around the proven Playwright login/session flow."""

    def __init__(self, settings: Settings, account: dict[str, Any] | None = None):
        self.settings = settings
        self.account = account or settings.account("default")
        self._playwright = None
        self.context = None
        self.page = None
        self.auth: dict[str, str] = {}

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        from export_chats import capture_auth, ensure_logged_in

        self._playwright = sync_playwright().start()
        cfg = self.settings.values["chatgpt"]
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.settings.browser_profile_for(self.account)),
            channel=cfg["browser_channel"],
            headless=False,
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        ensure_logged_in(self.page)
        self.auth = capture_auth(self.page)
        return self

    def __exit__(self, *_):
        if self.context:
            self.context.close()
        if self._playwright:
            self._playwright.stop()

    def _get(self, url: str) -> dict[str, Any]:
        from export_chats import fetch_with_session

        cfg = self.settings.values["chatgpt"]
        waits = [15, 30, 60]
        for attempt, wait in enumerate(waits, 1):
            response = fetch_with_session(self.page, url, self.auth)
            status = response["status"]
            if status == 200:
                try:
                    return json.loads(response["body"])
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"ChatGPT response schema is not valid JSON: {exc}") from exc
            if status in {401, 403}:
                raise RuntimeError("ChatGPT session expired. Sign in in the opened browser and retry sync.")
            if status == 404:
                raise FileNotFoundError(f"ChatGPT conversation endpoint returned 404: {url}")
            if status == 429 or status >= 500:
                if attempt < len(waits):
                    print(f"[SYNC] transient HTTP {status}; retrying in {wait}s", flush=True)
                    time.sleep(wait)
                    continue
            raise RuntimeError(f"ChatGPT request failed with HTTP {status}")
        raise RuntimeError("ChatGPT request failed after retries")

    def list_conversations(
        self,
        full_index: bool = False,
        local_rows: dict[str, dict[str, Any]] | None = None,
        stop_on_unchanged: bool = True,
    ) -> list[IndexItem]:
        base_url = self.settings.values["chatgpt"]["base_url"].rstrip("/")
        local_rows = local_rows or {}
        found: dict[str, IndexItem] = {}
        offset = 0
        total: int | None = None
        stopped_early = False
        while total is None or offset < total:
            data = self._get(f"{base_url}/backend-api/conversations?offset={offset}&limit=100&order=updated")
            batch = data.get("items") or []
            for item in batch:
                if item.get("id"):
                    cid = str(item["id"])
                    entry = IndexItem(
                        cid,
                        item.get("title") or "Untitled",
                        iso_utc(item.get("create_time")),
                        iso_utc(item.get("update_time")),
                        f"{base_url}/c/{cid}",
                    )
                    found[cid] = entry
                    if stop_on_unchanged and not full_index and _item_is_unchanged(local_rows, entry):
                        stopped_early = True
                        print(f"[SYNC] reached first unchanged conversation at {cid}; stopping index scan", flush=True)
                        break
            total = int(data.get("total") or len(found))
            offset += 100
            print(f"[SYNC] indexed {len(found)}/{total}", flush=True)
            if not batch or stopped_early:
                break
            time.sleep(random.uniform(0.4, 1.0))

        # Project chats are not guaranteed to be included in the main index.
        cursor = None
        projects: list[tuple[str, str]] = []
        while True:
            suffix = f"?cursor={cursor}" if cursor else ""
            data = self._get(f"{base_url}/backend-api/gizmos/snorlax/sidebar{suffix}")
            for item in data.get("items") or []:
                gizmo = (item.get("gizmo") or {}).get("gizmo") or item.get("gizmo") or {}
                if gizmo.get("id"):
                    projects.append((str(gizmo["id"]), (gizmo.get("display") or {}).get("name") or "Project"))
            next_cursor = data.get("cursor")
            if not data.get("items") or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        for project_id, name in projects:
            cursor = 0
            project_stopped = False
            while True:
                data = self._get(
                    f"{base_url}/backend-api/gizmos/{project_id}/conversations"
                    f"?cursor={cursor}&limit=50&owned_only=false"
                )
                batch = data.get("items") or []
                for item in batch:
                    if item.get("id"):
                        cid = str(item["id"])
                        entry = IndexItem(
                            cid,
                            item.get("title") or "Untitled",
                            iso_utc(item.get("create_time")),
                            iso_utc(item.get("update_time")),
                            f"{base_url}/g/{project_id}/c/{cid}",
                            project_id,
                        )
                        found[cid] = entry
                        if stop_on_unchanged and not full_index and _item_is_unchanged(local_rows, entry):
                            project_stopped = True
                            break
                next_cursor = data.get("cursor")
                if not batch or project_stopped or next_cursor in (None, cursor):
                    break
                cursor = next_cursor
            print(f"[SYNC] indexed project {name!r}", flush=True)
        return list(found.values())

    def fetch_conversation(self, item: IndexItem) -> dict[str, Any]:
        base_url = self.settings.values["chatgpt"]["base_url"].rstrip("/")
        return self._get(f"{base_url}/backend-api/conversation/{item.id}")

    def include_files(self) -> None:
        from export_chats import library_sweep

        def err(message: str):
            print(f"[FILES] {message}", flush=True)

        library_sweep(self.page, self.context, self.settings.root / "export", self.auth, err)


def _item_is_unchanged(local_rows: dict[str, dict[str, Any]], item: IndexItem) -> bool:
    local = local_rows.get(item.id)
    return bool(
        local
        and local.get("content_hash")
        and item.update_time
        and iso_utc(item.update_time) == iso_utc(local.get("updated_at"))
    )


def _local_index(settings: Settings, account_id: str = "default") -> dict[str, dict[str, Any]]:
    migrate(settings.database_path)
    with connect(settings.database_path) as conn:
        return {
            row["remote_id"]: dict(row)
            for row in conn.execute(
                "SELECT remote_id,updated_at,content_hash FROM conversations WHERE provider='chatgpt' AND account_id=?",
                (account_id,),
            )
        }


def run_sync(
    settings: Settings,
    *,
    full_index: bool = False,
    force_fetch: bool = False,
    include_files: bool = False,
    account_ids: list[str] | None = None,
) -> dict[str, Any]:
    migrate(settings.database_path)
    requested = set(account_ids or [])
    accounts = [
        account
        for account in settings.accounts
        if account.get("enabled", True) and (not requested or account["id"] in requested)
    ]
    missing = requested - {account["id"] for account in accounts}
    if missing:
        raise ValueError(f"Unknown or disabled account(s): {', '.join(sorted(missing))}")
    if not accounts:
        raise ValueError("No enabled ChatGPT accounts are configured")

    totals = {
        "index_items_seen": 0,
        "new_conversations": 0,
        "updated_conversations": 0,
        "unchanged_conversations": 0,
        "fetched_conversations": 0,
        "failed_conversations": 0,
        "new_messages": 0,
    }
    account_results = []
    # Browser profiles cannot be opened concurrently. Accounts are deliberately
    # synchronized one after another in configuration order.
    for account in accounts:
        if account.get("provider", "chatgpt") == "gemini":
            from .gemini import run_gemini_sync
            try:
                gemini = run_gemini_sync(settings, account, force_fetch=force_fetch)
                result = {
                    "account_id": account["id"], "account_name": account.get("name") or account["id"],
                    "provider": "gemini", "status": "partial" if gemini["failed"] else "complete",
                    "error": None, "index_items_seen": gemini["discovered"],
                    "new_conversations": gemini["new"], "updated_conversations": gemini["updated"],
                    "unchanged_conversations": gemini["unchanged"], "fetched_conversations": gemini["fetched"],
                    "failed_conversations": gemini["failed"], "new_messages": gemini["messages"],
                    "failed_cids": gemini["failed_cids"],
                    "failures": gemini["failures"],
                }
            except Exception as exc:
                result = {
                    "account_id": account["id"], "account_name": account.get("name") or account["id"],
                    "provider": "gemini", "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                    **{key: 0 for key in totals}, "failed_cids": [], "failures": [],
                }
        else:
            result = _sync_account(
                settings, account, full_index=full_index, force_fetch=force_fetch, include_files=include_files,
            )
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        account_results.append(result)
        with connect(settings.database_path) as conn:
            conn.execute(
                "INSERT INTO app_metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (
                    f"last_sync_status:{account['id']}",
                    json.dumps(result, ensure_ascii=False),
                ),
            )
        for key in totals:
            totals[key] += int(result.get(key, 0))

    failed_accounts = [item for item in account_results if item["status"] == "failed"]
    status = "failed" if len(failed_accounts) == len(account_results) else (
        "partial" if failed_accounts or totals["failed_conversations"] else "complete"
    )
    finished = datetime.now(timezone.utc).isoformat()
    overall = {"status": status, "finished_at": finished, **totals, "accounts": account_results}
    with connect(settings.database_path) as conn:
        conn.execute(
            "INSERT INTO app_metadata(key,value) VALUES('last_sync_status',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(overall, ensure_ascii=False),),
        )
    if status != "failed":
        from .analytics import refresh_analytics
        from .usage_time import refresh_usage_time
        refresh_analytics(settings.database_path, settings.timezone, settings.values.get("analytics", {}))
        refresh_usage_time(settings.database_path, settings.values.get("usage_time", {}))
    return overall


def _sync_account(
    settings: Settings,
    account: dict[str, Any],
    *,
    full_index: bool,
    force_fetch: bool,
    include_files: bool,
) -> dict[str, Any]:
    account_id = str(account["id"])
    account_name = str(account.get("name") or account_id)
    ensure_account(settings.database_path, account_id, account_name, "chatgpt")
    started = datetime.now(timezone.utc).isoformat()
    mode = "force" if force_fetch else "full-index" if full_index else "incremental"
    with connect(settings.database_path) as conn:
        run_id = conn.execute(
            "INSERT INTO sync_runs(started_at,mode,status,account_id) VALUES(?,?,?,?)",
            (started, mode, "running", account_id),
        ).lastrowid

    stats = {
        "index_items_seen": 0,
        "new_conversations": 0,
        "updated_conversations": 0,
        "unchanged_conversations": 0,
        "fetched_conversations": 0,
        "failed_conversations": 0,
        "new_messages": 0,
    }
    error: str | None = None
    try:
        print(f"[SYNC:{account_name}] Scanning conversation index...", flush=True)
        local_rows = _local_index(settings, account_id)
        with ChatGPTWebSource(settings, account) as source:
            remote = source.list_conversations(
                full_index=full_index or force_fetch,
                local_rows=local_rows,
                stop_on_unchanged=bool(
                    settings.values["chatgpt"].get("stop_on_first_unchanged", True)
                ),
            )
            plan = plan_sync(local_rows, remote, force_fetch=force_fetch)
            stats.update(
                index_items_seen=len(remote),
                new_conversations=len(plan.new),
                updated_conversations=len(plan.changed),
                unchanged_conversations=len(plan.unchanged),
            )
            queue = ordered_fetch_queue(remote, plan)
            print(
                f"[SYNC] visible={len(remote)} new={len(plan.new)} changed={len(plan.changed)} "
                f"unchanged={len(plan.unchanged)}",
                flush=True,
            )
            stop_fetch_on_unchanged = bool(
                settings.values["chatgpt"].get("stop_on_first_unchanged", True)
                and not full_index
                and not force_fetch
            )
            stopped_streams: set[str] = set()
            made_request = False
            for index, item in enumerate(queue, 1):
                stream = _index_stream(item)
                if stream in stopped_streams:
                    continue
                if made_request:
                    cfg = settings.values["chatgpt"]
                    time.sleep(random.uniform(cfg["min_delay_seconds"], cfg["max_delay_seconds"]))
                try:
                    data = source.fetch_conversation(item)
                    made_request = True
                    state, prompts = upsert_conversation(
                        settings,
                        data,
                        source_url=item.url,
                        project_id=item.project_id,
                        account_id=account_id,
                        account_name=account_name,
                    )
                    stats["fetched_conversations"] += 1
                    stats["new_messages"] += prompts
                    print(f"[FETCH {index}/{len(queue)}] {item.title}: {state}", flush=True)
                    if state == "unchanged" and stop_fetch_on_unchanged:
                        stopped_streams.add(stream)
                        skipped = sum(
                            1 for remaining in queue[index:]
                            if _index_stream(remaining) == stream
                        )
                        stream_name = "main" if stream == "__main__" else f"project:{stream}"
                        print(
                            f"[SYNC] first content-unchanged conversation in {stream_name}; "
                            f"skipping {skipped} older fetch candidate(s)",
                            flush=True,
                        )
                except Exception as exc:
                    made_request = True
                    stats["failed_conversations"] += 1
                    print(f"[FETCH {index}/{len(queue)}] {item.title}: failed ({type(exc).__name__})", flush=True)
            if include_files:
                source.include_files()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        finished = datetime.now(timezone.utc).isoformat()
        status = "failed" if error else ("partial" if stats["failed_conversations"] else "complete")
        with connect(settings.database_path) as conn:
            conn.execute(
                """
                UPDATE sync_runs SET finished_at=?,index_items_seen=?,new_conversations=?,
                  updated_conversations=?,unchanged_conversations=?,fetched_conversations=?,
                  failed_conversations=?,new_messages=?,status=?,error=? WHERE id=?
                """,
                (
                    finished,
                    stats["index_items_seen"],
                    stats["new_conversations"],
                    stats["updated_conversations"],
                    stats["unchanged_conversations"],
                    stats["fetched_conversations"],
                    stats["failed_conversations"],
                    stats["new_messages"],
                    status,
                    error,
                    run_id,
                ),
            )
            conn.execute(
                "INSERT INTO app_metadata(key,value) VALUES('last_sync_status',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (
                    json.dumps(
                        {
                            "status": status,
                            "finished_at": finished,
                            "account_id": account_id,
                            "account_name": account_name,
                            **stats,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.execute(
                "INSERT INTO app_metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (
                    f"last_sync_status:{account_id}",
                    json.dumps(
                        {
                            "status": status,
                            "finished_at": finished,
                            "account_id": account_id,
                            "account_name": account_name,
                            "error": error,
                            **stats,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
    return {
        "account_id": account_id,
        "account_name": account_name,
        "provider": "chatgpt",
        "status": "failed" if error else ("partial" if stats["failed_conversations"] else "complete"),
        "error": error,
        **stats,
    }


def last_sync_status(settings: Settings, account_id: str | None = None) -> dict[str, Any]:
    migrate(settings.database_path)
    with connect(settings.database_path) as conn:
        key = f"last_sync_status:{account_id}" if account_id else "last_sync_status"
        row = conn.execute("SELECT value FROM app_metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else {"status": "never"}
