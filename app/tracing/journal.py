import sqlite3
from datetime import datetime, timezone

from app.db import JournalError, connect, init_db


def log_llm_call(
    model: str,
    provider: str,
    mode: str,
    tokens: int = 0,
    latency_ms: int = 0,
    switch_reason: str | None = None,
) -> None:
    _insert(
        """
        INSERT INTO llm_calls (
            model, provider, mode, tokens, latency_ms, prompt_hash, switch_reason, created_at, client_id, event_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (model, provider, mode, tokens, latency_ms, None, switch_reason, _now(), None, None),
    )


def _insert(sql: str, values: tuple) -> None:
    try:
        init_db()
        with connect() as connection:
            connection.execute(sql, values)
    except JournalError:
        raise
    except sqlite3.Error as error:
        raise JournalError(f"Не удалось записать журнал: {error}") from error


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
