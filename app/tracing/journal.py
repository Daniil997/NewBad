import contextvars
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from app.db import JournalError, connect, init_db

_SCOPE: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar("trace_scope", default=None)
_SECRET = {"api_key", "apikey", "token", "authorization", "password", "secret", "access_token"}
_ITEM_KEYS = ("evidence_id", "source", "id", "title", "page")


@contextmanager
def trace_scope(client_id: str, event_id: str) -> Iterator[None]:
    token = _SCOPE.set((client_id, event_id))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def log_tool_call(tool: str, arguments: dict, result: object) -> None:
    stored_arguments = _public_arguments(arguments)
    stored_result = _public_result(result)
    payload = json.dumps(stored_result, ensure_ascii=False, sort_keys=True, default=str)
    client_id, event_id = _scope()
    _insert(
        """
        INSERT INTO tool_calls (tool, arguments, created_at, result_hash, result, client_id, event_id, query)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tool,
            json.dumps(stored_arguments, ensure_ascii=False),
            _now(),
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            payload,
            client_id,
            event_id,
            str(stored_arguments.get("query") or ""),
        ),
    )


def log_llm_call(
    model: str,
    provider: str,
    mode: str,
    tokens: int = 0,
    latency_ms: int = 0,
    switch_reason: str | None = None,
) -> None:
    client_id, event_id = _scope()
    _insert(
        """
        INSERT INTO llm_calls (
            model, provider, mode, tokens, latency_ms, prompt_hash, switch_reason, created_at, client_id, event_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (model, provider, mode, tokens, latency_ms, None, switch_reason, _now(), client_id, event_id),
    )


def log_step(client_id: str, event_id: str, step: str, detail: dict) -> None:
    _insert(
        """
        INSERT INTO agent_steps (client_id, event_id, step, detail, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (client_id, event_id, step, json.dumps(_public_detail(detail), ensure_ascii=False), _now()),
    )


def latest_steps(client_id: str, event_id: str) -> list[str]:
    return [item["step"] for item in _latest_step_rows(client_id, event_id)]


def read_trace(client_id: str, event_id: str, query: str | None = None) -> dict:
    init_db()
    try:
        with connect() as connection:
            tools = connection.execute(
                """
                SELECT tool, arguments, result, result_hash, query, created_at
                FROM tool_calls
                WHERE client_id = ? AND event_id = ?
                  AND (? IS NULL OR query = ?)
                ORDER BY id
                """,
                (client_id, event_id, query, query),
            ).fetchall()
            calls = connection.execute(
                """
                SELECT model, provider, mode, tokens, latency_ms, switch_reason, created_at
                FROM llm_calls
                WHERE client_id = ? AND event_id = ?
                ORDER BY id
                """,
                (client_id, event_id),
            ).fetchall()
    except sqlite3.Error as error:
        raise JournalError(f"Не удалось прочитать журнал: {error}") from error
    return {
        "client_id": client_id,
        "event_id": event_id,
        "query": query or "",
        "steps": _latest_step_rows(client_id, event_id),
        "tools": [_tool_row(row) for row in tools],
        "llm": [dict(row) for row in calls],
    }


def _latest_step_rows(client_id: str, event_id: str) -> list[dict]:
    init_db()
    try:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT step, detail, created_at FROM agent_steps
                WHERE client_id = ? AND event_id = ?
                ORDER BY id
                """,
                (client_id, event_id),
            ).fetchall()
    except sqlite3.Error as error:
        raise JournalError(f"Не удалось прочитать журнал: {error}") from error
    parsed = [
        {"step": row["step"], "detail": json.loads(row["detail"]), "created_at": row["created_at"]} for row in rows
    ]
    start = 0
    for index, item in enumerate(parsed):
        if item["step"] == "PLAN":
            start = index
    return parsed[start:]


def _insert(sql: str, values: tuple) -> None:
    try:
        init_db()
        with connect() as connection:
            connection.execute(sql, values)
    except JournalError:
        raise
    except sqlite3.Error as error:
        raise JournalError(f"Не удалось записать журнал: {error}") from error


def _scope() -> tuple[str | None, str | None]:
    current = _SCOPE.get()
    if current is None:
        return None, None
    return current


def _public_arguments(arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        return {}
    clean = {}
    for key, value in arguments.items():
        if str(key).lower() in _SECRET:
            continue
        clean[key] = value[:500] if isinstance(value, str) else value
    return clean


def _public_result(result: object) -> dict:
    if not isinstance(result, dict):
        return {"status": "ok"}
    items = result.get("items") if isinstance(result.get("items"), list) else []
    return {
        "status": result.get("status"),
        "evidence_id": result.get("evidence_id") or "",
        "source": result.get("source") or "",
        "message": str(result.get("message") or "")[:240],
        "items": [_brief_item(item) for item in items[:8]],
    }


def _brief_item(item: object) -> dict:
    if not isinstance(item, dict):
        return {}
    return {key: item[key] for key in _ITEM_KEYS if item.get(key) not in (None, "")}


def _public_detail(detail: dict) -> dict:
    if not isinstance(detail, dict):
        return {}
    return {key: value for key, value in detail.items() if str(key).lower() not in _SECRET}


def _tool_row(row: sqlite3.Row) -> dict:
    result = json.loads(row["result"]) if row["result"] else {}
    return {
        "tool": row["tool"],
        "query": row["query"] or "",
        "arguments": json.loads(row["arguments"]),
        "result": result,
        "result_hash": row["result_hash"],
        "created_at": row["created_at"],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
