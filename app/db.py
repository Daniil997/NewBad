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
    try:
        connection = sqlite3.connect(db_path())
    except sqlite3.Error as error:
        raise JournalError(f"Журнал недоступен: {error}") from error
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
                result_hash TEXT,
                result TEXT,
                client_id TEXT,
                event_id TEXT,
                query TEXT
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
                created_at TEXT NOT NULL,
                client_id TEXT,
                event_id TEXT
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
            CREATE TABLE IF NOT EXISTS llm_replay (
                prompt_hash TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                response TEXT NOT NULL,
                tokens INTEGER NOT NULL,
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
                created_at TEXT NOT NULL,
                source TEXT
            );
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(pasted_news)")}
        if "source" not in columns:
            connection.execute("ALTER TABLE pasted_news ADD COLUMN source TEXT")
        _add_column(connection, "tool_calls", "result", "TEXT")
        _add_column(connection, "tool_calls", "client_id", "TEXT")
        _add_column(connection, "tool_calls", "event_id", "TEXT")
        _add_column(connection, "tool_calls", "query", "TEXT")
        _add_column(connection, "llm_calls", "client_id", "TEXT")
        _add_column(connection, "llm_calls", "event_id", "TEXT")


def _add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if table not in {"tool_calls", "llm_calls"}:
        raise JournalError(f"Неизвестная таблица журнала: {table}")
    names = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in names:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


class JournalError(Exception):
    """Журнал не открылся или не записал строку. Повторный запуск сам по себе ошибкой не является."""


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
