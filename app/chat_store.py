"""Durable chat turns with retry-safe requests and one active answer per chat."""

import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.user_store import connect_app, now_utc


def recover_stale_turns(session_id: str) -> None:
    stale = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
    with connect_app() as connection:
        connection.execute(
            "UPDATE profile_chat_requests SET status = 'failed' "
            "WHERE session_id = ? AND status = 'pending' AND updated_at < ?", (session_id, stale),
        )


def start_turn(session_id: str, question: str, request_id: str | None) -> tuple[str, dict | None, str]:
    key = request_id or secrets.token_hex(16)
    now = now_utc()
    stale = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
    with connect_app() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM profile_chat_sessions WHERE id = ?", (session_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Диалог не найден")
        connection.execute(
            "UPDATE profile_chat_requests SET status = 'failed' "
            "WHERE session_id = ? AND status = 'pending' AND updated_at < ?", (session_id, stale),
        )
        old = connection.execute(
            "SELECT r.*, m.text FROM profile_chat_requests r JOIN profile_chat_messages m "
            "ON m.id = r.user_message_id WHERE r.session_id = ? AND r.request_id = ?", (session_id, key),
        ).fetchone()
        if old and old["text"] != question:
            raise HTTPException(status_code=409, detail="Этот запрос уже использован для другого сообщения")
        if old and old["status"] == "completed":
            return key, json.loads(old["response_json"]), now
        pending = connection.execute(
            "SELECT 1 FROM profile_chat_requests WHERE session_id = ? AND status = 'pending'", (session_id,),
        ).fetchone()
        if pending:
            raise HTTPException(status_code=409, detail="В этом чате ещё готовится ответ. Можно открыть другой диалог.")
        if old:
            connection.execute(
                "UPDATE profile_chat_requests SET status = 'pending', updated_at = ? "
                "WHERE session_id = ? AND request_id = ?", (now, session_id, key),
            )
        else:
            cursor = connection.execute(
                "INSERT INTO profile_chat_messages (session_id, role, text, created_at) VALUES (?, 'user', ?, ?)",
                (session_id, question, now),
            )
            connection.execute(
                "INSERT INTO profile_chat_requests VALUES (?, ?, ?, 'pending', NULL, ?)",
                (session_id, key, cursor.lastrowid, now),
            )
            connection.execute(
                "UPDATE profile_chat_sessions SET title = CASE WHEN title = 'Общий чат по бизнесу' "
                "THEN ? ELSE title END, updated_at = ? WHERE id = ?", (question[:80], now, session_id),
            )
        connection.execute("UPDATE profile_chat_sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    return key, None, now


def fail_turn(session_id: str, key: str, lease: str) -> None:
    with connect_app() as connection:
        connection.execute(
            "UPDATE profile_chat_requests SET status = 'failed', updated_at = ? "
            "WHERE session_id = ? AND request_id = ? AND status = 'pending' AND updated_at = ?",
            (now_utc(), session_id, key, lease),
        )


def finish_turn(session_id: str, key: str, result: dict, profile_version: int, lease: str) -> dict:
    now = now_utc()
    response = {"role": "assistant", "text": result["answer"], "citations": result["citations"], "created_at": now}
    with connect_app() as connection:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            "SELECT 1 FROM profile_chat_requests WHERE session_id = ? AND request_id = ? "
            "AND status = 'pending' AND updated_at = ?", (session_id, key, lease),
        ).fetchone()
        if not active:
            raise HTTPException(status_code=409, detail="Этот ответ уже заменён повторным запросом. Обновите диалог.")
        connection.execute(
            "INSERT INTO profile_chat_messages (session_id, role, text, citations_json, created_at) "
            "VALUES (?, 'assistant', ?, ?, ?)",
            (session_id, response["text"], json.dumps(response["citations"], ensure_ascii=False), now),
        )
        connection.execute(
            "UPDATE profile_chat_requests SET status = 'completed', response_json = ?, updated_at = ? "
            "WHERE session_id = ? AND request_id = ?",
            (json.dumps(response, ensure_ascii=False), now, session_id, key),
        )
        connection.execute(
            "UPDATE profile_chat_sessions SET profile_version = ?, updated_at = ? WHERE id = ?",
            (profile_version, now, session_id),
        )
    return response
