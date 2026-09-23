from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .db import connect

ANALYTICS_VERSION = 2


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
    else:
        clauses.append(f"{alias}.provider IN ('chatgpt','gemini')")
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
            lifecycle_rows = rows if conversation["provider"] == "wechat" else prompts
            prompt_times = [datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) for row in prompts]
            days = {_local_date(row["created_at"], timezone_name) for row in lifecycle_rows} - {None}
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
            first = lifecycle_rows[0]["created_at"] if lifecycle_rows else None
            last = lifecycle_rows[-1]["created_at"] if lifecycle_rows else None
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
                    "prompts": 0, "turns": 0, "prompt_visible_tokens": 0, "response_visible_tokens": 0,
                    "total_visible_tokens": 0, "first_activity": row["created_at"], "last_activity": row["created_at"],
                })
                tokens = row["visible_tokens"] or 0
                item["turns"] += 1
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
                """INSERT INTO daily_conversation_activity(
                     activity_date,conversation_id,prompts,turns,prompt_visible_tokens,
                     response_visible_tokens,total_visible_tokens,first_activity,last_activity
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (day, conversation_id, item["prompts"], item["turns"], item["prompt_visible_tokens"],
                 item["response_visible_tokens"], item["total_visible_tokens"], item["first_activity"], item["last_activity"]),
            )

        daily: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for (day, conversation_id), item in daily_conversations.items():
            conversation = by_id[conversation_id]
            target = daily[(day, conversation["provider"], conversation["account_id"])]
            for field in ("prompts", "turns", "prompt_visible_tokens", "response_visible_tokens", "total_visible_tokens"):
                target[field] += item[field]
            if item["turns"] if conversation["provider"] == "wechat" else item["prompts"]:
                target["active_conversations"] += 1
        for conversation in conversations:
            day = _local_date(conversation["created_at"], timezone_name)
            if day:
                daily[(day, conversation["provider"], conversation["account_id"])]["new_conversations"] += 1
        for (day, provider, account_id), item in daily.items():
            conn.execute(
                """INSERT INTO daily_activity(
                     activity_date,provider,account_id,prompts,turns,prompt_visible_tokens,
                     response_visible_tokens,total_visible_tokens,active_conversations,new_conversations,computed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (day, provider, account_id, item["prompts"], item["turns"], item["prompt_visible_tokens"],
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
            COALESCE(SUM(s.prompts),0) outbound_messages,
            COALESCE(SUM(s.turns-s.prompts),0) inbound_messages,
            COALESCE(SUM(s.turns),0) total_messages,
            COALESCE(SUM(s.prompt_visible_tokens),0) prompt_visible_tokens,
            COALESCE(SUM(s.response_visible_tokens),0) response_visible_tokens,
            COALESCE(SUM(s.total_visible_tokens),0) total_visible_tokens,
            MIN(s.first_activity) first_activity,MAX(s.last_activity) latest_activity
            FROM conversations c LEFT JOIN conversation_stats s ON s.conversation_id=c.id WHERE 1=1 {where}""", values
        ).fetchone()
        active = conn.execute(
            f"SELECT COUNT(DISTINCT d.activity_date) n FROM daily_conversation_activity d "
            f"JOIN conversations c ON c.id=d.conversation_id "
            f"WHERE ((c.provider='wechat' AND d.turns>0) OR (c.provider<>'wechat' AND d.prompts>0)) {where}", values
        ).fetchone()["n"]
    return {**dict(row), "active_days": active}


def daily_series(db_path, timezone_name="UTC", provider="", account_id="") -> list[dict[str, Any]]:
    clauses, values = [], []
    if provider:
        clauses.append("provider=?"); values.append(provider)
    else:
        clauses.append("provider IN ('chatgpt','gemini')")
    if account_id:
        clauses.append("account_id=?"); values.append(account_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT activity_date date,SUM(prompts) prompts,SUM(turns) turns,
            SUM(prompts) outbound_messages,SUM(turns-prompts) inbound_messages,SUM(turns) total_messages,
            SUM(prompt_visible_tokens) prompt_visible_tokens,
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
    metrics = ("prompts", "turns", "outbound_messages", "inbound_messages", "total_messages", "prompt_visible_tokens", "response_visible_tokens", "total_visible_tokens", "active_conversations", "new_conversations")
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
            f"JOIN conversations c ON c.id=d.conversation_id "
            f"WHERE ((c.provider='wechat' AND d.turns>0) OR (c.provider<>'wechat' AND d.prompts>0)) {where}", values
        ).fetchall()
    unique: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        date = datetime.fromisoformat(row["activity_date"])
        key = f"{date.isocalendar().year}-W{date.isocalendar().week:02d}" if granularity == "weekly" else row["activity_date"][:7]
        unique[key].add(row["conversation_id"])
    for key in grouped:
        grouped[key]["active_conversations"] = len(unique[key])
    return [grouped[key] for key in sorted(grouped)]


SORT_EXPRESSIONS = {"total_visible_tokens":"s.total_visible_tokens","prompt_visible_tokens":"s.prompt_visible_tokens","response_visible_tokens":"s.response_visible_tokens","prompts":"s.prompts","turns":"s.turns","total_messages":"s.turns","outbound_messages":"s.prompts","inbound_messages":"(s.turns-s.prompts)","active_days":"s.active_days","calendar_span_days":"s.calendar_span_days","session_count":"s.session_count","estimated_active_seconds":"s.estimated_active_seconds","created_at":"c.created_at","updated_at":"c.updated_at"}


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
            f"""SELECT c.id,c.provider,c.account_id,a.name account_name,a.alias account_alias,
            CASE WHEN a.alias IS NOT NULL AND trim(a.alias)<>'' THEN a.name || ' · ' || a.alias
                 WHEN (SELECT COUNT(*) FROM accounts ax WHERE ax.provider=a.provider AND ax.name=a.name)>1
                 THEN a.name || ' · …' || substr(COALESCE(a.external_user_id,a.id),-4)
                 ELSE a.name END account_display_name,
            c.title,c.created_at,c.updated_at,c.model_hint,c.conversation_type,
            COALESCE(s.prompts,0) prompts,COALESCE(s.turns,0) turns,COALESCE(s.prompt_visible_tokens,0) prompt_visible_tokens,
            COALESCE(s.response_visible_tokens,0) response_visible_tokens,COALESCE(s.total_visible_tokens,0) total_visible_tokens,
            COALESCE(s.prompts,0) outbound_messages,COALESCE(s.turns-s.prompts,0) inbound_messages,
            COALESCE(s.turns,0) total_messages,
            s.calendar_span_days,s.active_days,s.session_count,s.estimated_active_seconds,s.longest_gap_seconds,
            (SELECT json_group_array(json_object('id',q.id,'name',q.name,'color',q.color)) FROM
              (SELECT t.id,t.name,t.color,SUM(mt.weight) w FROM message_topics mt JOIN messages mm ON mm.id=mt.message_id
               JOIN topics t0 ON t0.id=mt.topic_id JOIN topics t ON t.id=COALESCE(t0.parent_id,t0.id)
               WHERE mm.conversation_id=c.id GROUP BY t.id ORDER BY w DESC LIMIT 3) q) topics
            FROM conversations c JOIN accounts a ON a.id=c.account_id LEFT JOIN conversation_stats s ON s.conversation_id=c.id
            WHERE (c.title LIKE ? OR EXISTS (
                SELECT 1 FROM messages sm
                WHERE sm.conversation_id=c.id AND sm.is_active_branch=1 AND sm.visible_text LIKE ?
            )) {where} {topic_filter} ORDER BY {order} DESC,c.updated_at DESC LIMIT ? OFFSET ?""",
            [f"%{search}%", f"%{search}%", *values, topic_id, topic_id, topic_id, max(1, min(limit, 5000)), max(0, offset)]
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
        overview = conn.execute("""SELECT c.id,c.provider,c.account_id,a.name account_name,a.alias account_alias,
        CASE WHEN a.alias IS NOT NULL AND trim(a.alias)<>'' THEN a.name || ' · ' || a.alias
             WHEN (SELECT COUNT(*) FROM accounts ax WHERE ax.provider=a.provider AND ax.name=a.name)>1
             THEN a.name || ' · …' || substr(COALESCE(a.external_user_id,a.id),-4)
             ELSE a.name END account_display_name,
        c.title,c.created_at,c.updated_at,c.model_hint,c.conversation_type,
        COALESCE(s.prompts,0) prompts,COALESCE(s.turns,0) turns,COALESCE(s.prompt_visible_tokens,0) prompt_visible_tokens,
        COALESCE(s.response_visible_tokens,0) response_visible_tokens,COALESCE(s.total_visible_tokens,0) total_visible_tokens,
        COALESCE(s.prompts,0) outbound_messages,COALESCE(s.turns-s.prompts,0) inbound_messages,
        COALESCE(s.turns,0) total_messages,
        s.calendar_span_days,s.active_days,s.session_count,s.estimated_active_seconds,s.longest_gap_seconds
        FROM conversations c JOIN accounts a ON a.id=c.account_id LEFT JOIN conversation_stats s ON s.conversation_id=c.id WHERE c.id=?""", (conversation_id,)).fetchone()
        if not overview:
            return None
        messages = conn.execute("""SELECT m.id,m.role,m.direction,m.sender_external_id,m.sender_display_name,m.raw_type,
        m.created_at,m.model,m.content_type,m.visible_text,m.visible_tokens,m.sequence_index,m.has_attachment,m.analyzable
        FROM messages m JOIN conversations c ON c.id=m.conversation_id
        WHERE m.conversation_id=? AND m.is_active_branch=1
        ORDER BY CASE WHEN c.provider='wechat' THEN m.created_at ELSE '' END,m.sequence_index,m.id""", (conversation_id,)).fetchall()
        topics = conn.execute("SELECT t.id,t.name,t.color,p.name parent_name,SUM(mt.weight) prompt_share FROM message_topics mt JOIN topics t ON t.id=mt.topic_id LEFT JOIN topics p ON p.id=t.parent_id JOIN messages m ON m.id=mt.message_id WHERE m.conversation_id=? GROUP BY t.id ORDER BY prompt_share DESC", (conversation_id,)).fetchall()
    return {**dict(overview), "messages": [dict(row) for row in messages], "topics": [dict(row) for row in topics]}


def day_conversations(db_path, day, provider="", account_id=""):
    where, values = _scope(provider, account_id)
    with connect(db_path) as conn:
        rows = conn.execute(f"""SELECT c.id,c.provider,c.account_id,a.name account_name,a.alias account_alias,
        CASE WHEN a.alias IS NOT NULL AND trim(a.alias)<>'' THEN a.name || ' · ' || a.alias
             WHEN (SELECT COUNT(*) FROM accounts ax WHERE ax.provider=a.provider AND ax.name=a.name)>1
             THEN a.name || ' · …' || substr(COALESCE(a.external_user_id,a.id),-4)
             ELSE a.name END account_display_name,c.title,c.conversation_type,
        d.prompts,d.turns,d.prompts outbound_messages,(d.turns-d.prompts) inbound_messages,d.turns total_messages,d.prompt_visible_tokens,
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
    conversations = conversation_rankings(db_path, "total_messages", 5000, provider=provider)
    return {
        "busiest_day_by_prompts": max(days, key=lambda row: row["prompts"], default=None),
        "busiest_day_by_tokens": max(days, key=lambda row: row["total_visible_tokens"], default=None),
        "busiest_day_by_messages": max(days, key=lambda row: row["total_messages"], default=None),
        "most_messages": max(conversations, key=lambda row: row["total_messages"], default=None),
        "most_outbound": max(conversations, key=lambda row: row["outbound_messages"], default=None),
        "most_inbound": max(conversations, key=lambda row: row["inbound_messages"], default=None),
        "most_active_days": max(conversations, key=lambda row: row.get("active_days") or 0, default=None),
        "longest_lifecycle": max(conversations, key=lambda row: row.get("calendar_span_days") or 0, default=None),
        "largest_user_prompts": message_rankings(db_path, "user", 5, provider),
        "largest_assistant_responses": message_rankings(db_path, "assistant", 5, provider),
    }
