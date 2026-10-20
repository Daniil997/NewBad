import hashlib
import json
from datetime import datetime, timezone

from app.db import connect, init_db


def log_tool_call(tool: str, arguments: dict, result: object) -> None:
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    _insert(
        """
        INSERT INTO tool_calls (tool, arguments, created_at, result_hash)
        VALUES (?, ?, ?, ?)
        """,
        (tool, json.dumps(arguments, ensure_ascii=False), _now(), digest),
    )


def log_llm_call(model: str, provider: str, mode: str, tokens: int = 0, switch_reason: str | None = None) -> None:
    _insert(
        """
        INSERT INTO llm_calls (model, provider, mode, tokens, latency_ms, prompt_hash, switch_reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (model, provider, mode, tokens, 0, None, switch_reason, _now()),
    )


def log_step(client_id: str, event_id: str, step: str, detail: dict) -> None:
    _insert(
        """
        INSERT INTO agent_steps (client_id, event_id, step, detail, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (client_id, event_id, step, json.dumps(detail, ensure_ascii=False), _now()),
    )


def latest_steps(client_id: str, event_id: str) -> list[str]:
    init_db()
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT step FROM agent_steps
            WHERE client_id = ? AND event_id = ?
            ORDER BY id
            """,
            (client_id, event_id),
        ).fetchall()
    steps = [row["step"] for row in rows]
    start = 0
    for index, step in enumerate(steps):
        if step == "PLAN":
            start = index
    return steps[start:]


def _insert(sql: str, values: tuple) -> None:
    init_db()
    with connect() as connection:
        connection.execute(sql, values)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
