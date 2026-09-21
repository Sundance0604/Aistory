from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 2

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL DEFAULT 'default' REFERENCES accounts(id),
    remote_id TEXT,
    title TEXT NOT NULL DEFAULT 'Untitled',
    created_at TEXT,
    updated_at TEXT,
    fetched_at TEXT NOT NULL,
    current_node_id TEXT,
    content_hash TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    model_hint TEXT,
    raw_json_path TEXT,
    source_url TEXT,
    project_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_title ON conversations(title);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    parent_id TEXT,
    role TEXT NOT NULL,
    created_at TEXT,
    model TEXT,
    content_type TEXT,
    visible_text TEXT NOT NULL DEFAULT '',
    visible_tokens INTEGER NOT NULL DEFAULT 0,
    tokenizer_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    sequence_index INTEGER,
    is_active_branch INTEGER NOT NULL DEFAULT 0,
    has_attachment INTEGER NOT NULL DEFAULT 0,
    raw_metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);
CREATE INDEX IF NOT EXISTS idx_messages_active ON messages(is_active_branch);
CREATE INDEX IF NOT EXISTS idx_messages_sequence ON messages(conversation_id, sequence_index);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT NOT NULL,
    index_items_seen INTEGER NOT NULL DEFAULT 0,
    new_conversations INTEGER NOT NULL DEFAULT 0,
    updated_conversations INTEGER NOT NULL DEFAULT 0,
    unchanged_conversations INTEGER NOT NULL DEFAULT 0,
    fetched_conversations INTEGER NOT NULL DEFAULT 0,
    failed_conversations INTEGER NOT NULL DEFAULT 0,
    new_messages INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    kind TEXT NOT NULL,
    location TEXT,
    storage_method TEXT NOT NULL DEFAULT 'filesystem',
    imported_at TEXT NOT NULL,
    items_seen INTEGER NOT NULL DEFAULT 0,
    items_imported INTEGER NOT NULL DEFAULT 0,
    items_failed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_data_sources_account ON data_sources(account_id, imported_at);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES topics(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    level INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_topics (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    weight REAL NOT NULL CHECK(weight >= 0 AND weight <= 1),
    classifier TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    PRIMARY KEY(message_id, topic_id, classifier_version)
);
CREATE INDEX IF NOT EXISTS idx_message_topics_topic ON message_topics(topic_id);

CREATE TABLE IF NOT EXISTS classification_failures (
    message_id TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    error TEXT NOT NULL,
    PRIMARY KEY(message_id, classifier_version)
);
"""


class ClosingConnection(sqlite3.Connection):
    """sqlite transaction context that also releases Windows file handles."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO accounts(id,name,created_at,updated_at) VALUES('default','默认账号',?,?)",
            (now, now),
        )
        conversation_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(conversations)")
        }
        if "account_id" not in conversation_columns:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN account_id TEXT NOT NULL DEFAULT 'default'"
            )
        if "remote_id" not in conversation_columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN remote_id TEXT")
        conn.execute("UPDATE conversations SET remote_id=id WHERE remote_id IS NULL")
        sync_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sync_runs)")}
        if "account_id" not in sync_columns:
            conn.execute("ALTER TABLE sync_runs ADD COLUMN account_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_account_remote "
            "ON conversations(account_id, remote_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_account ON conversations(account_id)"
        )
        conn.execute(
            "INSERT INTO app_metadata(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )


def ensure_account(path: str | Path, account_id: str, name: str) -> None:
    migrate(path)
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO accounts(id,name,created_at,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at
            """,
            (account_id, name, now, now),
        )


@contextmanager
def transaction(path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
