from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .db import connect

ANALYTICS_VERSION = 1


def _local_date(timestamp: str | None, timezone_name: str) -> str | None:
    if not timestamp:
        return None
    value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return value.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def _scope(provider: str = "", account_id: str = "", alias: str = "c") -> tuple[str, list[str]]:
    clauses, values = [], []
    if provider:
        clauses.append(f"{alias}.provider=?")
        values.append(provider)
    if account_id:
        clauses.append(f"{alias}.account_id=?")
        values.append(account_id)
    return ((" AND " + " AND ".join(clauses)) if clauses else ""), values


def analytics_signature(timezone_name: str, config: dict[str, Any]) -> str:
    payload = {"version": ANALYTICS_VERSION, "timezone": timezone_name, **config}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def refresh_analytics(db_path, timezone_name: str, config: dict[str, Any]) -> dict[str, int]:
    """Rebuild derived tables after import/sync or a semantic setting change."""
    gap_seconds = int(config.get("session_gap_minutes", 30)) * 60
    single_seconds = int(config.get("single_prompt_minutes", 5)) * 60
    tail_seconds = int(config.get("session_tail_minutes", 5)) * 60
    computed_at = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conversations = conn.execute("SELECT id,provider,account_id,created_at FROM conversations").fetchall()
        messages = conn.execute(
            "SELECT conversation_id,role,created_at,visible_tokens FROM messages "
            "WHERE is_active_branch=1 AND role IN ('user','assistant') AND created_at IS NOT NULL "
            "ORDER BY conversation_id,created_at,sequence_index"
        ).fetchall()
        grouped: dict[str, list[Any]] = defaultdict(list)
        by_id = {row["id"]: row for row in conversations}
        for row in messages:
            grouped[row["conversation_id"]].append(row)

        conn.execute("DELETE FROM conversation_stats")
        conn.execute("DELETE FROM daily_activity")
        conn.execute("DELETE FROM daily_conversation_activity")
        daily_conversations: dict[tuple[str, str], dict[str, Any]] = {}

        for conversation in conversations:
            rows = grouped.get(conversation["id"], [])
            prompts = [row for row in rows if row["role"] == "user"]
            prompt_times = [datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) for row in prompts]
            days = {_local_date(row["created_at"], timezone_name) for row in prompts} - {None}
            sessions: list[list[datetime]] = []
            for stamp in prompt_times:
                if not sessions or (stamp - sessions[-1][-1]).total_seconds() > gap_seconds:
                    sessions.append([stamp])
                else:
                    sessions[-1].append(stamp)
            gaps = [(b - a).total_seconds() for a, b in zip(prompt_times, prompt_times[1:])]
            estimated = sum(
                single_seconds if len(session) == 1 else int((session[-1] - session[0]).total_seconds()) + tail_seconds
                for session in sessions
            )
            first = prompts[0]["created_at"] if prompts else None
            last = prompts[-1]["created_at"] if prompts else None
            calendar_span = (datetime.fromisoformat(_local_date(last, timezone_name)) - datetime.fromisoformat(_local_date(first, timezone_name))).days + 1 if first and last else 0
            prompt_tokens = sum(row["visible_tokens"] or 0 for row in prompts)
            response_tokens = sum(row["visible_tokens"] or 0 for row in rows if row["role"] == "assistant")
            conn.execute(
                "INSERT INTO conversation_stats VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (conversation["id"], first, last, calendar_span, len(days), len(sessions), estimated,
                 int(max(gaps, default=0)), len(prompts), len(rows), prompt_tokens, response_tokens,
                 prompt_tokens + response_tokens, computed_at),
            )
            for row in rows:
                day = _local_date(row["created_at"], timezone_name)
                if not day:
                    continue
                key = (day, conversation["id"])
                item = daily_conversations.setdefault(key, {
                    "prompts": 0, "prompt_visible_tokens": 0, "response_visible_tokens": 0,
                    "total_visible_tokens": 0, "first_activity": row["created_at"], "last_activity": row["created_at"],
                })
                tokens = row["visible_tokens"] or 0
                item["total_visible_tokens"] += tokens
                if row["role"] == "user":
                    item["prompts"] += 1
                    item["prompt_visible_tokens"] += tokens
                else:
                    item["response_visible_tokens"] += tokens
                item["first_activity"] = min(item["first_activity"], row["created_at"])
                item["last_activity"] = max(item["last_activity"], row["created_at"])

        for (day, conversation_id), item in daily_conversations.items():
            conn.execute(
                "INSERT INTO daily_conversation_activity VALUES(?,?,?,?,?,?,?,?)",
                (day, conversation_id, item["prompts"], item["prompt_visible_tokens"],
                 item["response_visible_tokens"], item["total_visible_tokens"], item["first_activity"], item["last_activity"]),
            )

        daily: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for (day, conversation_id), item in daily_conversations.items():
            conversation = by_id[conversation_id]
            target = daily[(day, conversation["provider"], conversation["account_id"])]
            for field in ("prompts", "prompt_visible_tokens", "response_visible_tokens", "total_visible_tokens"):
                target[field] += item[field]
            if item["prompts"]:
                target["active_conversations"] += 1
        for conversation in conversations:
            day = _local_date(conversation["created_at"], timezone_name)
            if day:
                daily[(day, conversation["provider"], conversation["account_id"])]["new_conversations"] += 1
        for (day, provider, account_id), item in daily.items():
            conn.execute(
                "INSERT INTO daily_activity VALUES(?,?,?,?,?,?,?,?,?,?)",
                (day, provider, account_id, item["prompts"], item["prompt_visible_tokens"],
                 item["response_visible_tokens"], item["total_visible_tokens"], item["active_conversations"],
                 item["new_conversations"], computed_at),
            )
        conn.execute(
            "INSERT INTO app_metadata(key,value) VALUES('analytics_signature',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (analytics_signature(timezone_name, config),),
        )
    return {"conversations": len(conversations), "days": len(daily)}


def ensure_analytics(db_path, timezone_name: str, config: dict[str, Any]) -> None:
    expected = analytics_signature(timezone_name, config)
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM app_metadata WHERE key='analytics_signature'").fetchone()
    if not row or row["value"] != expected:
        refresh_analytics(db_path, timezone_name, config)


def summary(db_path, timezone_name="UTC", provider="", account_id="") -> dict[str, Any]:
    where, values = _scope(provider, account_id)
    with connect(db_path) as conn:
        row = conn.execute(
            f"""SELECT COUNT(*) conversations,COALESCE(SUM(s.prompts),0) prompts,
            COALESCE(SUM(s.prompt_visible_tokens),0) prompt_visible_tokens,
            COALESCE(SUM(s.response_visible_tokens),0) response_visible_tokens,
            COALESCE(SUM(s.total_visible_tokens),0) total_visible_tokens,
            MIN(s.first_activity) first_activity,MAX(s.last_activity) latest_activity
            FROM conversations c LEFT JOIN conversation_stats s ON s.conversation_id=c.id WHERE 1=1 {where}""", values
        ).fetchone()
        active = conn.execute(
            f"SELECT COUNT(DISTINCT d.activity_date) n FROM daily_conversation_activity d "
            f"JOIN conversations c ON c.id=d.conversation_id WHERE d.prompts>0 {where}", values
        ).fetchone()["n"]
    return {**dict(row), "active_days": active}


def daily_series(db_path, timezone_name="UTC", provider="", account_id="") -> list[dict[str, Any]]:
    clauses, values = [], []
    if provider:
        clauses.append("provider=?"); values.append(provider)
    if account_id:
        clauses.append("account_id=?"); values.append(account_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT activity_date date,SUM(prompts) prompts,SUM(prompt_visible_tokens) prompt_visible_tokens,
            SUM(response_visible_tokens) response_visible_tokens,SUM(total_visible_tokens) total_visible_tokens,
            SUM(active_conversations) active_conversations,SUM(new_conversations) new_conversations
            FROM daily_activity {where} GROUP BY activity_date ORDER BY activity_date""", values
        ).fetchall()
    return [dict(row) for row in rows]


def aggregate_series(db_path, timezone_name, granularity, provider="", account_id="") -> list[dict[str, Any]]:
    if granularity == "daily":
        return daily_series(db_path, timezone_name, provider, account_id)
    if granularity not in {"weekly", "monthly"}:
        raise ValueError("granularity must be daily, weekly, or monthly")
    grouped: dict[str, dict[str, Any]] = {}
    metrics = ("prompts", "prompt_visible_tokens", "response_visible_tokens", "total_visible_tokens", "active_conversations", "new_conversations")
    for row in daily_series(db_path, timezone_name, provider, account_id):
        date = datetime.fromisoformat(row["date"])
        key = f"{date.isocalendar().year}-W{date.isocalendar().week:02d}" if granularity == "weekly" else row["date"][:7]
        item = grouped.setdefault(key, {"date": key, **{field: 0 for field in metrics}})
        for field in metrics:
            item[field] += row[field]
    where, values = _scope(provider, account_id)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT d.activity_date,d.conversation_id FROM daily_conversation_activity d "
            f"JOIN conversations c ON c.id=d.conversation_id WHERE d.prompts>0 {where}", values
        ).fetchall()
    unique: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        date = datetime.fromisoformat(row["activity_date"])
        key = f"{date.isocalendar().year}-W{date.isocalendar().week:02d}" if granularity == "weekly" else row["activity_date"][:7]
        unique[key].add(row["conversation_id"])
    for key in grouped:
        grouped[key]["active_conversations"] = len(unique[key])
    return [grouped[key] for key in sorted(grouped)]


SORT_EXPRESSIONS = {"total_visible_tokens":"s.total_visible_tokens","prompt_visible_tokens":"s.prompt_visible_tokens","response_visible_tokens":"s.response_visible_tokens","prompts":"s.prompts","turns":"s.turns","created_at":"c.created_at","updated_at":"c.updated_at"}


def conversation_rankings(db_path, sort="total_visible_tokens", limit=50, offset=0, search="", account_id="", provider="", topic_id=0):
    order = SORT_EXPRESSIONS.get(sort, SORT_EXPRESSIONS["total_visible_tokens"])
    where, values = _scope(provider, account_id)
    topic_id = max(0, int(topic_id or 0))
    topic_filter = """
      AND (?=0 OR EXISTS (
        SELECT 1 FROM messages tm
        JOIN message_topics tmt ON tmt.message_id=tm.id
        JOIN topics tt ON tt.id=tmt.topic_id
        WHERE tm.conversation_id=c.id AND (tt.id=? OR tt.parent_id=?)
      ))
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT c.id,c.provider,c.account_id,a.name account_name,c.title,c.created_at,c.updated_at,c.model_hint,
            COALESCE(s.prompts,0) prompts,COALESCE(s.turns,0) turns,COALESCE(s.prompt_visible_tokens,0) prompt_visible_tokens,
            COALESCE(s.response_visible_tokens,0) response_visible_tokens,COALESCE(s.total_visible_tokens,0) total_visible_tokens,
            s.calendar_span_days,s.active_days,s.session_count,s.estimated_active_seconds,s.longest_gap_seconds,
            (SELECT json_group_array(json_object('id',q.id,'name',q.name,'color',q.color)) FROM
              (SELECT t.id,t.name,t.color,SUM(mt.weight) w FROM message_topics mt JOIN messages mm ON mm.id=mt.message_id
               JOIN topics t0 ON t0.id=mt.topic_id JOIN topics t ON t.id=COALESCE(t0.parent_id,t0.id)
               WHERE mm.conversation_id=c.id GROUP BY t.id ORDER BY w DESC LIMIT 3) q) topics
            FROM conversations c JOIN accounts a ON a.id=c.account_id LEFT JOIN conversation_stats s ON s.conversation_id=c.id
            WHERE c.title LIKE ? {where} {topic_filter} ORDER BY {order} DESC,c.updated_at DESC LIMIT ? OFFSET ?""",
            [f"%{search}%", *values, topic_id, topic_id, topic_id, max(1, min(limit, 5000)), max(0, offset)]
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["topics"] = json.loads(item["topics"] or "[]")
        result.append(item)
    return result


def message_rankings(db_path, role, limit=20, provider=""):
    if role not in {"user", "assistant"}:
        raise ValueError("role must be user or assistant")
    where, values = _scope(provider)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT m.id,m.conversation_id,c.title,m.created_at,m.visible_tokens,substr(m.visible_text,1,240) preview "
            f"FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.is_active_branch=1 AND m.role=? {where} "
            f"ORDER BY m.visible_tokens DESC LIMIT ?", [role, *values, max(1, min(limit, 200))]
        ).fetchall()
    return [dict(row) for row in rows]


def conversation_detail(db_path, conversation_id):
    with connect(db_path) as conn:
        overview = conn.execute("""SELECT c.id,c.provider,c.account_id,a.name account_name,c.title,c.created_at,c.updated_at,c.model_hint,
        COALESCE(s.prompts,0) prompts,COALESCE(s.turns,0) turns,COALESCE(s.prompt_visible_tokens,0) prompt_visible_tokens,
        COALESCE(s.response_visible_tokens,0) response_visible_tokens,COALESCE(s.total_visible_tokens,0) total_visible_tokens,
        s.calendar_span_days,s.active_days,s.session_count,s.estimated_active_seconds,s.longest_gap_seconds
        FROM conversations c JOIN accounts a ON a.id=c.account_id LEFT JOIN conversation_stats s ON s.conversation_id=c.id WHERE c.id=?""", (conversation_id,)).fetchone()
        if not overview:
            return None
        messages = conn.execute("SELECT id,role,created_at,model,content_type,visible_text,visible_tokens,sequence_index,has_attachment,analyzable FROM messages WHERE conversation_id=? AND is_active_branch=1 ORDER BY sequence_index", (conversation_id,)).fetchall()
        topics = conn.execute("SELECT t.id,t.name,t.color,p.name parent_name,SUM(mt.weight) prompt_share FROM message_topics mt JOIN topics t ON t.id=mt.topic_id LEFT JOIN topics p ON p.id=t.parent_id JOIN messages m ON m.id=mt.message_id WHERE m.conversation_id=? GROUP BY t.id ORDER BY prompt_share DESC", (conversation_id,)).fetchall()
    return {**dict(overview), "messages": [dict(row) for row in messages], "topics": [dict(row) for row in topics]}


def day_conversations(db_path, day, provider="", account_id=""):
    where, values = _scope(provider, account_id)
    with connect(db_path) as conn:
        rows = conn.execute(f"""SELECT c.id,c.provider,c.account_id,a.name account_name,c.title,d.prompts,d.prompt_visible_tokens,
        d.response_visible_tokens,d.total_visible_tokens,d.first_activity,d.last_activity,
        (SELECT t.name FROM message_topics mt JOIN messages m ON m.id=mt.message_id JOIN topics t0 ON t0.id=mt.topic_id
         JOIN topics t ON t.id=COALESCE(t0.parent_id,t0.id) WHERE m.conversation_id=c.id GROUP BY t.id ORDER BY SUM(mt.weight) DESC LIMIT 1) dominant_topic,
        (SELECT t.color FROM message_topics mt JOIN messages m ON m.id=mt.message_id JOIN topics t0 ON t0.id=mt.topic_id
         JOIN topics t ON t.id=COALESCE(t0.parent_id,t0.id) WHERE m.conversation_id=c.id GROUP BY t.id ORDER BY SUM(mt.weight) DESC LIMIT 1) topic_color
        FROM daily_conversation_activity d JOIN conversations c ON c.id=d.conversation_id JOIN accounts a ON a.id=c.account_id
        WHERE d.activity_date=? {where} ORDER BY d.total_visible_tokens DESC""", [day, *values]).fetchall()
    return [dict(row) for row in rows]


def lifecycle(db_path, provider="", account_id=""):
    return conversation_rankings(db_path, "updated_at", 5000, 0, "", account_id, provider)


def records(db_path, timezone_name, provider=""):
    days = daily_series(db_path, timezone_name, provider)
    return {"busiest_day_by_prompts": max(days, key=lambda row: row["prompts"], default=None), "busiest_day_by_tokens": max(days, key=lambda row: row["total_visible_tokens"], default=None), "largest_user_prompts": message_rankings(db_path, "user", 5, provider), "largest_assistant_responses": message_rankings(db_path, "assistant", 5, provider)}
