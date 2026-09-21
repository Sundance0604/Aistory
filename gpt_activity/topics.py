from __future__ import annotations

import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .config import Settings
from .db import connect, migrate


class TopicWeight(BaseModel):
    parent_topic: str = Field(min_length=1, max_length=80)
    topic: str = Field(min_length=1, max_length=100)
    weight: float = Field(ge=0, le=1)

    @field_validator("parent_topic", "topic")
    @classmethod
    def tidy(cls, value: str) -> str:
        return " ".join(value.strip().split())


class TopicClassificationResult(BaseModel):
    topics: list[TopicWeight] = Field(min_length=1, max_length=3)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def normalize_weights(self):
        total = sum(item.weight for item in self.topics)
        if total <= 0:
            raise ValueError("topic weights must sum to a positive number")
        for item in self.topics:
            item.weight = item.weight / total
        return self


@dataclass(frozen=True)
class TopicClassificationRequest:
    conversation_title: str
    prompt: str
    previous_user_prompts: tuple[str, ...]
    existing_taxonomy: tuple[str, ...]


class TopicClassifier(Protocol):
    classifier_name: str
    classifier_version: str

    def classify(self, request: TopicClassificationRequest) -> TopicClassificationResult: ...


class DeepSeekTopicClassifier:
    classifier_name = "deepseek"

    def __init__(self, settings: Settings):
        import httpx

        self.settings = settings
        cfg = settings.values["topics"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.api_key = settings.api_key
        self.timeout = cfg["request_timeout_seconds"]
        self.classifier_version = f"deepseek:{self.model}:{cfg['classifier_schema_version']}"
        if not self.api_key:
            raise ValueError("No topic API key configured. Add it to config.local.json or GPT_ACTIVITY_API_KEY.")
        self.client = httpx.Client(timeout=self.timeout)

    def close(self) -> None:
        self.client.close()

    def classify(self, request: TopicClassificationRequest) -> TopicClassificationResult:
        import httpx

        system = (
            "Classify the user's current prompt into one to three hierarchical topics. "
            "Return JSON only. Reuse canonical topics when appropriate and avoid label explosion. "
            "Weights must be non-negative and sum to 1. Format: "
            '{"topics":[{"parent_topic":"Mathematics","topic":"Abstract Algebra","weight":1.0}],'
            '"confidence":0.9}.'
        )
        payload = {
            "conversation_title": request.conversation_title,
            "previous_user_prompts": list(request.previous_user_prompts),
            "current_user_prompt": request.prompt,
            "existing_taxonomy": list(request.existing_taxonomy),
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "Classify this JSON context:\n" + json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 700,
        }
        last_error: Exception | None = None
        for attempt in range(2):
            if attempt:
                body["messages"].append(
                    {"role": "user", "content": "Repair the previous output. Return valid JSON matching the requested schema."}
                )
            try:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return TopicClassificationResult.model_validate_json(content)
            except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
        raise RuntimeError(f"topic classification failed after repair attempt: {type(last_error).__name__}")


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w\u3400-\u9fff]+", "-", value, flags=re.UNICODE).strip("-")
    return value[:120] or "topic"


def _taxonomy(conn) -> tuple[str, ...]:
    rows = conn.execute(
        """
        SELECT CASE WHEN p.name IS NULL THEN t.name ELSE p.name || ' / ' || t.name END AS label
        FROM topics t LEFT JOIN topics p ON p.id=t.parent_id ORDER BY t.level,t.name
        """
    ).fetchall()
    return tuple(row["label"] for row in rows)


def _topic_id(conn, parent_name: str, topic_name: str, now: str) -> int:
    parent_slug = _slug(parent_name)
    conn.execute(
        "INSERT INTO topics(parent_id,name,slug,level,created_at) VALUES(NULL,?,?,1,?) "
        "ON CONFLICT(slug) DO UPDATE SET name=excluded.name",
        (parent_name, parent_slug, now),
    )
    parent_id = conn.execute("SELECT id FROM topics WHERE slug=?", (parent_slug,)).fetchone()["id"]
    child_slug = f"{parent_slug}/{_slug(topic_name)}"
    conn.execute(
        "INSERT INTO topics(parent_id,name,slug,level,created_at) VALUES(?,?,?,2,?) "
        "ON CONFLICT(slug) DO UPDATE SET name=excluded.name,parent_id=excluded.parent_id",
        (parent_id, topic_name, child_slug, now),
    )
    return conn.execute("SELECT id FROM topics WHERE slug=?", (child_slug,)).fetchone()["id"]


def classify_prompts(settings: Settings, *, reclassify: bool = False, limit: int | None = None) -> dict[str, int]:
    migrate(settings.database_path)
    classifier = DeepSeekTopicClassifier(settings)
    max_chars = int(settings.values["topics"]["max_context_chars"])
    with connect(settings.database_path) as conn:
        where = "" if reclassify else "AND NOT EXISTS (SELECT 1 FROM message_topics mt WHERE mt.message_id=m.id)"
        query = f"""
            SELECT m.id,m.conversation_id,m.sequence_index,m.visible_text,c.title
            FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE m.is_active_branch=1 AND m.role='user' {where}
            ORDER BY m.created_at,m.conversation_id,m.sequence_index
        """
        params: tuple[Any, ...] = ()
        if limit:
            query += " LIMIT ?"
            params = (limit,)
        prompts = [dict(row) for row in conn.execute(query, params).fetchall()]

    completed = failed = 0

    def process(prompt: dict[str, Any]) -> tuple[str, str, str | None]:
        with connect(settings.database_path) as conn:
            previous = conn.execute(
                """
                SELECT visible_text FROM messages WHERE conversation_id=? AND is_active_branch=1
                  AND role='user' AND sequence_index<? ORDER BY sequence_index DESC LIMIT 2
                """,
                (prompt["conversation_id"], prompt["sequence_index"]),
            ).fetchall()
            taxonomy = _taxonomy(conn)
        request = TopicClassificationRequest(
            conversation_title=prompt["title"],
            prompt=prompt["visible_text"][:max_chars],
            previous_user_prompts=tuple(row["visible_text"][:600] for row in reversed(previous)),
            existing_taxonomy=taxonomy,
        )
        now = datetime.now(timezone.utc).isoformat()
        try:
            result = classifier.classify(request)
            with connect(settings.database_path) as conn:
                if reclassify:
                    conn.execute("DELETE FROM message_topics WHERE message_id=?", (prompt["id"],))
                for item in result.topics:
                    topic_id = _topic_id(conn, item.parent_topic, item.topic, now)
                    conn.execute(
                        """
                        INSERT INTO message_topics(message_id,topic_id,weight,classifier,classifier_version,classified_at)
                        VALUES(?,?,?,?,?,?)
                        ON CONFLICT(message_id,topic_id,classifier_version)
                        DO UPDATE SET weight=excluded.weight,classified_at=excluded.classified_at
                        """,
                        (
                            prompt["id"], topic_id, item.weight, classifier.classifier_name,
                            classifier.classifier_version, now,
                        ),
                    )
                conn.execute(
                    "DELETE FROM classification_failures WHERE message_id=? AND classifier_version=?",
                    (prompt["id"], classifier.classifier_version),
                )
            return prompt["title"], "classified", None
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            with connect(settings.database_path) as conn:
                conn.execute(
                    """
                    INSERT INTO classification_failures(message_id,classifier_version,attempted_at,error)
                    VALUES(?,?,?,?) ON CONFLICT(message_id,classifier_version)
                    DO UPDATE SET attempted_at=excluded.attempted_at,error=excluded.error
                    """,
                    (prompt["id"], classifier.classifier_version, now, error),
                )
            return prompt["title"], "failed", type(exc).__name__

    try:
        workers = max(1, min(int(settings.values["topics"].get("max_concurrency", 4)), 8))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="topic") as pool:
            futures = [pool.submit(process, prompt) for prompt in prompts]
            for index, future in enumerate(as_completed(futures), 1):
                title, status, error_name = future.result()
                if status == "classified":
                    completed += 1
                    print(f"[TOPICS {index}/{len(prompts)}] {title}: classified", flush=True)
                else:
                    failed += 1
                    print(f"[TOPICS {index}/{len(prompts)}] failed ({error_name})", flush=True)
    finally:
        classifier.close()
    return {"queued": len(prompts), "classified": completed, "failed": failed}


def _turn_tokens(conn, message: dict[str, Any]) -> int:
    next_user = conn.execute(
        "SELECT sequence_index FROM messages WHERE conversation_id=? AND is_active_branch=1 "
        "AND role='user' AND sequence_index>? ORDER BY sequence_index LIMIT 1",
        (message["conversation_id"], message["sequence_index"]),
    ).fetchone()
    end = next_user["sequence_index"] if next_user else 1_000_000_000
    response = conn.execute(
        "SELECT COALESCE(SUM(visible_tokens),0) AS tokens FROM messages WHERE conversation_id=? "
        "AND is_active_branch=1 AND role='assistant' AND sequence_index>? AND sequence_index<?",
        (message["conversation_id"], message["sequence_index"], end),
    ).fetchone()["tokens"]
    return (message["visible_tokens"] or 0) + (response or 0)


def topic_distribution(db_path, level: int = 1) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        prompt_rows = [dict(row) for row in conn.execute(
            "SELECT id,conversation_id,sequence_index,visible_tokens FROM messages "
            "WHERE is_active_branch=1 AND role='user'"
        )]
        turn_tokens = {row["id"]: _turn_tokens(conn, row) for row in prompt_rows}
        rows = conn.execute(
            """
            SELECT mt.message_id,mt.weight,t.id,t.name,t.parent_id,p.name parent_name
            FROM message_topics mt JOIN topics t ON t.id=mt.topic_id
            LEFT JOIN topics p ON p.id=t.parent_id
            """
        ).fetchall()
        totals: dict[int, dict[str, Any]] = {}
        for row in rows:
            topic_id = row["parent_id"] if level == 1 else row["id"]
            name = row["parent_name"] if level == 1 else row["name"]
            item = totals.setdefault(topic_id, {"id": topic_id, "name": name, "prompt_share": 0.0, "token_share": 0.0})
            item["prompt_share"] += row["weight"]
            item["token_share"] += turn_tokens.get(row["message_id"], 0) * row["weight"]
        prompt_total = sum(item["prompt_share"] for item in totals.values()) or 1
        token_total = sum(item["token_share"] for item in totals.values()) or 1
        for item in totals.values():
            item["prompt_percent"] = item["prompt_share"] / prompt_total * 100
            item["token_percent"] = item["token_share"] / token_total * 100
        return sorted(totals.values(), key=lambda item: item["token_share"], reverse=True)


def topic_timeline(db_path, timezone_name: str) -> list[dict[str, Any]]:
    from zoneinfo import ZoneInfo

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT m.id,m.conversation_id,m.sequence_index,m.visible_tokens,m.created_at,
                   mt.weight,t.id,t.name,p.id parent_id,p.name parent_name
            FROM message_topics mt JOIN messages m ON m.id=mt.message_id
            JOIN topics t ON t.id=mt.topic_id LEFT JOIN topics p ON p.id=t.parent_id
            WHERE m.created_at IS NOT NULL
            """
        ).fetchall()
        totals: dict[tuple[str, int], dict[str, Any]] = {}
        for raw in rows:
            row = dict(raw)
            month = datetime.fromisoformat(row["created_at"]).astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m")
            key = (month, row["parent_id"])
            item = totals.setdefault(
                key,
                {"period": month, "topic_id": row["parent_id"], "topic": row["parent_name"], "prompt_share": 0.0, "token_share": 0.0},
            )
            item["prompt_share"] += row["weight"]
            item["token_share"] += _turn_tokens(conn, row) * row["weight"]
        return sorted(totals.values(), key=lambda item: (item["period"], item["topic"]))


def topic_detail(db_path, topic_id: int) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        if not topic:
            return None
        rows = conn.execute(
            """
            SELECT c.id,c.title,SUM(mt.weight) prompt_share,
                   SUM(m.visible_tokens*mt.weight) prompt_token_share
            FROM message_topics mt JOIN topics t ON t.id=mt.topic_id
            JOIN messages m ON m.id=mt.message_id JOIN conversations c ON c.id=m.conversation_id
            WHERE t.id=? OR t.parent_id=? GROUP BY c.id ORDER BY prompt_share DESC LIMIT 100
            """,
            (topic_id, topic_id),
        ).fetchall()
        return {**dict(topic), "conversations": [dict(row) for row in rows]}
