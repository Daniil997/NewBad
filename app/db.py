import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    raw = os.environ.get("DATA_DIR", "data")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "journal.sqlite3"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(db_path())
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY,
                tool TEXT NOT NULL,
                arguments TEXT NOT NULL,
                created_at TEXT NOT NULL,
                result_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                mode TEXT NOT NULL,
                tokens INTEGER,
                latency_ms INTEGER,
                prompt_hash TEXT,
                switch_reason TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feed_cache (
                client_id TEXT NOT NULL,
                corpus_version TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (client_id, corpus_version)
            );
            CREATE TABLE IF NOT EXISTS agent_steps (
                id INTEGER PRIMARY KEY,
                client_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                step TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pasted_news (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                excerpt TEXT NOT NULL,
                url TEXT NOT NULL,
                event_type TEXT,
                event_title TEXT,
                published_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def journal_tables() -> list[str]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name IN ('llm_calls', 'tool_calls')
            ORDER BY name
            """
        ).fetchall()
    return [row["name"] for row in rows]


def journal_is_empty() -> bool:
    with connect() as connection:
        tool_count = connection.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"]
        llm_count = connection.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()["n"]
    return tool_count == 0 and llm_count == 0
