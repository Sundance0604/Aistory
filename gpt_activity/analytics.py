from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .db import connect


def _local_date(timestamp: str | None, timezone_name: str) -> str | None:
    if not timestamp:
        return None
    value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return value.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def summary(db_path, timezone_name: str = "UTC") -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM conversations) AS conversations,
              SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) AS prompts,
              SUM(CASE WHEN role='user' THEN visible_tokens ELSE 0 END) AS prompt_visible_tokens,
              SUM(CASE WHEN role='assistant' THEN visible_tokens ELSE 0 END) AS response_visible_tokens,
              SUM(CASE WHEN role IN ('user','assistant') THEN visible_tokens ELSE 0 END) AS total_visible_tokens,
              MIN(CASE WHEN role='user' THEN created_at END) AS first_activity,
              MAX(CASE WHEN role='user' THEN created_at END) AS latest_activity
            FROM messages WHERE is_active_branch=1
            """
        ).fetchone()
        result = {key: (row[key] or 0) for key in row.keys()}
        timestamps = conn.execute(
            "SELECT created_at FROM messages WHERE is_active_branch=1 AND role='user' AND created_at IS NOT NULL"
        ).fetchall()
        result["active_days"] = len(
            {_local_date(item["created_at"], timezone_name) for item in timestamps}
        )
        return result


def daily_series(db_path, timezone_name: str) -> list[dict[str, Any]]:
    days: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "prompts": 0,
            "prompt_visible_tokens": 0,
            "response_visible_tokens": 0,
            "total_visible_tokens": 0,
            "active_conversation_ids": set(),
            "new_conversations": 0,
        }
    )
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT conversation_id,role,created_at,visible_tokens FROM messages "
            "WHERE is_active_branch=1 AND role IN ('user','assistant') AND created_at IS NOT NULL"
        ).fetchall()
        for row in rows:
            day = _local_date(row["created_at"], timezone_name)
            if not day:
                continue
            item = days[day]
            tokens = row["visible_tokens"] or 0
            item["total_visible_tokens"] += tokens
            if row["role"] == "user":
                item["prompts"] += 1
                item["prompt_visible_tokens"] += tokens
                item["active_conversation_ids"].add(row["conversation_id"])
            else:
                item["response_visible_tokens"] += tokens
        for row in conn.execute("SELECT created_at FROM conversations WHERE created_at IS NOT NULL"):
            day = _local_date(row["created_at"], timezone_name)
            if day:
                days[day]["new_conversations"] += 1
    result = []
    for day in sorted(days):
        item = days[day]
        result.append(
            {
                "date": day,
                "prompts": item["prompts"],
                "prompt_visible_tokens": item["prompt_visible_tokens"],
                "response_visible_tokens": item["response_visible_tokens"],
                "total_visible_tokens": item["total_visible_tokens"],
                "active_conversations": len(item["active_conversation_ids"]),
                "new_conversations": item["new_conversations"],
            }
        )
    return result


def aggregate_series(db_path, timezone_name: str, granularity: str) -> list[dict[str, Any]]:
    if granularity == "daily":
        return daily_series(db_path, timezone_name)
    grouped: dict[str, dict[str, Any]] = {}
    for row in daily_series(db_path, timezone_name):
        date = datetime.fromisoformat(row["date"])
        if granularity == "weekly":
            year, week, _ = date.isocalendar()
            key = f"{year}-W{week:02d}"
        elif granularity == "monthly":
            key = row["date"][:7]
        else:
            raise ValueError("granularity must be daily, weekly, or monthly")
        item = grouped.setdefault(
            key,
            {
                "date": key,
                "prompts": 0,
                "prompt_visible_tokens": 0,
                "response_visible_tokens": 0,
                "total_visible_tokens": 0,
                "active_conversations": 0,
                "new_conversations": 0,
            },
        )
        for metric in item:
            if metric != "date":
                item[metric] += row[metric]

    # A conversation active on multiple days must still count only once in the
    # selected week/month. Daily totals cannot preserve that distinction.
    active_conversations: dict[str, set[str]] = defaultdict(set)
    with connect(db_path) as conn:
        prompt_rows = conn.execute(
            "SELECT conversation_id,created_at FROM messages "
            "WHERE is_active_branch=1 AND role='user' AND created_at IS NOT NULL"
        ).fetchall()
    for row in prompt_rows:
        local_day = _local_date(row["created_at"], timezone_name)
        if not local_day:
            continue
        date = datetime.fromisoformat(local_day)
        if granularity == "weekly":
            year, week, _ = date.isocalendar()
            key = f"{year}-W{week:02d}"
        else:
            key = local_day[:7]
        active_conversations[key].add(row["conversation_id"])
    for key, item in grouped.items():
        item["active_conversations"] = len(active_conversations[key])
    return [grouped[key] for key in sorted(grouped)]


SORT_EXPRESSIONS = {
    "total_visible_tokens": "total_visible_tokens",
    "prompt_visible_tokens": "prompt_visible_tokens",
    "response_visible_tokens": "response_visible_tokens",
    "prompts": "prompts",
    "turns": "turns",
    "created_at": "c.created_at",
    "updated_at": "c.updated_at",
}


def conversation_rankings(
    db_path,
    sort: str = "total_visible_tokens",
    limit: int = 50,
    offset: int = 0,
    search: str = "",
    account_id: str = "",
):
    order = SORT_EXPRESSIONS.get(sort, SORT_EXPRESSIONS["total_visible_tokens"])
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT c.id,c.account_id,a.name account_name,c.title,c.created_at,c.updated_at,c.model_hint,
              SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) prompts,
              SUM(CASE WHEN m.role IN ('user','assistant') THEN 1 ELSE 0 END) turns,
              SUM(CASE WHEN m.role='user' THEN m.visible_tokens ELSE 0 END) prompt_visible_tokens,
              SUM(CASE WHEN m.role='assistant' THEN m.visible_tokens ELSE 0 END) response_visible_tokens,
              SUM(CASE WHEN m.role IN ('user','assistant') THEN m.visible_tokens ELSE 0 END) total_visible_tokens
            FROM conversations c
            JOIN accounts a ON a.id=c.account_id
            LEFT JOIN messages m ON m.conversation_id=c.id AND m.is_active_branch=1
            WHERE c.title LIKE ? AND (?='' OR c.account_id=?)
            GROUP BY c.id
            ORDER BY {order} DESC, c.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (f"%{search}%", account_id, account_id, max(1, min(limit, 500)), max(0, offset)),
        ).fetchall()
        return [dict(row) for row in rows]


def message_rankings(db_path, role: str, limit: int = 20):
    if role not in {"user", "assistant"}:
        raise ValueError("role must be user or assistant")
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT m.id,m.conversation_id,c.title,m.created_at,m.visible_tokens,
                   substr(m.visible_text,1,240) AS preview
            FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE m.is_active_branch=1 AND m.role=?
            ORDER BY m.visible_tokens DESC LIMIT ?
            """,
            (role, max(1, min(limit, 200))),
        ).fetchall()
        return [dict(row) for row in rows]


def conversation_detail(db_path, conversation_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        overview_row = conn.execute(
            """
            SELECT c.id,c.account_id,a.name account_name,c.title,c.created_at,c.updated_at,c.model_hint,
              SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) prompts,
              SUM(CASE WHEN m.role IN ('user','assistant') THEN 1 ELSE 0 END) turns,
              SUM(CASE WHEN m.role='user' THEN m.visible_tokens ELSE 0 END) prompt_visible_tokens,
              SUM(CASE WHEN m.role='assistant' THEN m.visible_tokens ELSE 0 END) response_visible_tokens,
              SUM(CASE WHEN m.role IN ('user','assistant') THEN m.visible_tokens ELSE 0 END) total_visible_tokens
            FROM conversations c
            JOIN accounts a ON a.id=c.account_id
            LEFT JOIN messages m ON m.conversation_id=c.id AND m.is_active_branch=1
            WHERE c.id=? GROUP BY c.id
            """,
            (conversation_id,),
        ).fetchone()
        if not overview_row:
            return None
        overview = dict(overview_row)
        messages = conn.execute(
            """
            SELECT id,role,created_at,model,content_type,visible_text,visible_tokens,
                   sequence_index,has_attachment
            FROM messages WHERE conversation_id=? AND is_active_branch=1
            ORDER BY sequence_index
            """,
            (conversation_id,),
        ).fetchall()
        overview["messages"] = [dict(row) for row in messages]
        topic_rows = conn.execute(
            """
            SELECT t.id,t.name,p.name parent_name,SUM(mt.weight) prompt_share
            FROM message_topics mt JOIN topics t ON t.id=mt.topic_id
            LEFT JOIN topics p ON p.id=t.parent_id
            JOIN messages m ON m.id=mt.message_id
            WHERE m.conversation_id=? GROUP BY t.id ORDER BY prompt_share DESC
            """,
            (conversation_id,),
        ).fetchall()
        overview["topics"] = [dict(row) for row in topic_rows]
        return overview


def records(db_path, timezone_name: str) -> dict[str, Any]:
    days = daily_series(db_path, timezone_name)
    busiest_prompts = max(days, key=lambda item: item["prompts"], default=None)
    busiest_tokens = max(days, key=lambda item: item["total_visible_tokens"], default=None)
    return {
        "busiest_day_by_prompts": busiest_prompts,
        "busiest_day_by_tokens": busiest_tokens,
        "largest_user_prompts": message_rankings(db_path, "user", 5),
        "largest_assistant_responses": message_rankings(db_path, "assistant", 5),
    }
