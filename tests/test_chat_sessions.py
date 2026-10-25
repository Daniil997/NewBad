"""Chat persistence, retry safety, isolation, and user-supplied evidence."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from app.agent import generate
from app.agent.business_context import company_context, conversation_evidence
from app.llm.router import Route, TierResult
from app.main import app
from app.user_store import connect_app


def csrf(client):
    return {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}


def register(client, login):
    result = client.post("/api/auth/register", headers=csrf(client),
                         json={"login": login, "password": "correct-horse-battery"})
    assert result.status_code == 201


def setup(monkeypatch):
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    monkeypatch.setattr("app.main._fresh_real_news", lambda _days: [])
    monkeypatch.setattr("app.main.market_snapshot", lambda: {"items": []})


def reply(question, history, evidence):
    return {"answer": "Учтём условия из вашего сообщения.", "evidence_ids": ["user-current"]}


def test_sessions_survive_return_and_do_not_leak_between_accounts(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr("app.main.answer_profile_chat", reply)
    with TestClient(app) as owner, TestClient(app) as other:
        register(owner, "chat_owner")
        register(other, "chat_other")
        headers = csrf(owner)
        first = owner.post("/api/real/chat/sessions", headers=headers, json={}).json()["id"]
        assert owner.post("/api/real/chat/sessions", headers=headers, json={}).json()["id"] == first
        second = owner.post("/api/real/chat/sessions", headers=headers, json={"new_chat": True}).json()["id"]
        assert second != first
        sent = owner.post(f"/api/real/chat/sessions/{first}/messages", headers=headers,
                          json={"question": "У нас плавающая ставка 18%", "request_id": "turn-1"})
        assert sent.status_code == 200
        assert sent.json()["citations"][0]["kind"] == "user_statement"
        restored = owner.get(f"/api/real/chat/sessions/{first}").json()
        assert [row["role"] for row in restored["messages"]] == ["user", "assistant"]
        assert restored["title"] == "У нас плавающая ставка 18%"
        assert other.get(f"/api/real/chat/sessions/{first}").status_code == 404
        assert other.patch(f"/api/real/chat/sessions/{first}", headers=csrf(other), json={"title": "Чужой"}).status_code == 404
        assert other.get("/api/real/chat/sessions").json()["sessions"] == []
        assert owner.patch(f"/api/real/chat/sessions/{first}", json={"title": "Без CSRF"}).status_code == 403
        assert owner.patch(f"/api/real/chat/sessions/{first}", headers=headers, json={"title": "Кредиты"}).status_code == 200
        assert owner.get(f"/api/real/chat/sessions/{first}").json()["title"] == "Кредиты"
        with connect_app() as connection:
            saved = connection.execute("SELECT profile_json FROM business_profiles WHERE owner_id = ?",
                                       (owner.get("/api/auth/me").json()["user"]["id"],)).fetchone()
        assert "18%" not in saved[0]  # chat details do not silently edit the profile


def test_failed_turn_is_saved_and_retry_does_not_duplicate_messages(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr("app.main.answer_profile_chat", lambda *_args: None)
    with TestClient(app) as client:
        register(client, "retry_owner")
        headers = csrf(client)
        session = client.post("/api/real/chat/sessions", headers=headers, json={}).json()["id"]
        url = f"/api/real/chat/sessions/{session}/messages"
        body = {"question": "Импортируем 35% сырья", "request_id": "same-request"}
        assert client.post(url, headers=headers, json=body).status_code == 503
        stored = client.get(f"/api/real/chat/sessions/{session}").json()["messages"]
        assert len(stored) == 1 and stored[0]["request_status"] == "failed"
        calls = []

        def success(*args):
            calls.append(args)
            return reply(*args)

        monkeypatch.setattr("app.main.answer_profile_chat", success)
        first = client.post(url, headers=headers, json=body)
        repeated = client.post(url, headers=headers, json=body)
        assert first.status_code == repeated.status_code == 200
        assert first.json() == repeated.json() and len(calls) == 1
        assert len(client.get(f"/api/real/chat/sessions/{session}").json()["messages"]) == 2
        assert client.post(url, headers=headers, json={**body, "question": "Другой вопрос"}).status_code == 409
        assert client.post(url, headers=headers, json={"question": "   "}).status_code == 422


def test_one_answer_at_a_time_per_chat_but_other_chats_work(monkeypatch):
    setup(monkeypatch)
    entered, release = Event(), Event()

    def wait_reply(*args):
        entered.set()
        assert release.wait(10)
        return reply(*args)

    monkeypatch.setattr("app.main.answer_profile_chat", wait_reply)
    with TestClient(app) as client:
        register(client, "parallel_owner")
        headers = csrf(client)
        session = client.post("/api/real/chat/sessions", headers=headers, json={}).json()["id"]
        url = f"/api/real/chat/sessions/{session}/messages"
        with ThreadPoolExecutor() as pool:
            first = pool.submit(client.post, url, headers=headers, json={"question": "Условия кредита", "request_id": "one"})
            try:
                assert entered.wait(10)
                assert client.post(url, headers=headers, json={"question": "Повтор", "request_id": "two"}).status_code == 409
                assert client.get(f"/api/real/chat/sessions/{session}").json()["messages"][0]["request_status"] == "pending"
                assert client.post("/api/real/chat/sessions", headers=headers, json={"new_chat": True}).status_code == 200
            finally:
                release.set()
            assert first.result().status_code == 200


def test_user_numbers_are_grounded_but_assistant_inventions_are_not(monkeypatch):
    seen = []

    def model(messages, **_kwargs):
        seen.append(messages)
        return TierResult(Route(mode="mock", provider="mock", model="mock-model"), json.dumps({
            "answer": "Учитываем указанную вами долю импорта 35%.", "evidence_ids": ["user-message:0"],
        }, ensure_ascii=False))

    monkeypatch.setattr(generate, "complete_tier", model)
    monkeypatch.setattr(generate, "developer_settings", lambda: {})
    history = [{"role": "user", "content": "Импортируем 35% сырья"}]
    history += [{"role": "assistant", "content": "Затраты вырастут на 99%."}] * 12
    result = generate.answer_profile_chat("А что с поставками?", history, [])
    assert result and "35%" in result["answer"]
    evidence = json.loads(seen[0][1]["content"])["evidence"]
    assert any("35%" in row["text"] for row in evidence)
    assert all("99%" not in row["text"] for row in evidence)
    packet = conversation_evidence(history, "Теперь доля импорта 20%")
    assert packet[-1]["text"] == "Теперь доля импорта 20%"


def test_shared_context_preserves_unknown_no_and_interests():
    context = company_context({"company_name": "Пример", "country": "Казахстан", "city": "Алматы",
                               "credit_interest": "interested", "product_credit": "no",
                               "product_deposit": "unknown", "annual_revenue": None})
    assert context["country"] == "Казахстан" and context["city"] == "Алматы"
    assert context["financial_interests"]["credit_interest"] == "interested"
    assert context["company_details"]["product_credit"] == "no"
    assert "product_deposit" not in context["company_details"]
    assert "annual_revenue" not in context["company_details"]


def test_expired_generation_is_retryable_and_cannot_overwrite_new_answer(monkeypatch):
    from datetime import datetime, timedelta, timezone
    import pytest
    from fastapi import HTTPException
    from app.chat_store import finish_turn, start_turn

    setup(monkeypatch)
    with TestClient(app) as client:
        register(client, "expired_owner")
        session = client.post("/api/real/chat/sessions", headers=csrf(client), json={}).json()["id"]
        key, _, old_lease = start_turn(session, "Первый вопрос", "old-key")
        with connect_app() as connection:
            connection.execute("UPDATE profile_chat_requests SET updated_at = ? WHERE session_id = ?",
                               ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), session))
        restored = client.get(f"/api/real/chat/sessions/{session}").json()
        assert restored["messages"][0]["request_status"] == "failed"
        _, _, new_lease = start_turn(session, "Первый вопрос", key)
        result = {"answer": "Новый ответ", "citations": []}
        with pytest.raises(HTTPException):
            finish_turn(session, key, result, 1, old_lease)
        finish_turn(session, key, result, 1, new_lease)
        assert len(client.get(f"/api/real/chat/sessions/{session}").json()["messages"]) == 2


def test_delete_chat_checks_owner_and_csrf_and_removes_history(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr("app.main.answer_profile_chat", reply)
    with TestClient(app) as owner, TestClient(app) as other:
        register(owner, "delete_owner")
        register(other, "delete_other")
        headers = csrf(owner)
        session = owner.post("/api/real/chat/sessions", headers=headers, json={}).json()["id"]
        kept = owner.post("/api/real/chat/sessions", headers=headers, json={"new_chat": True}).json()["id"]
        url = f"/api/real/chat/sessions/{session}"
        assert owner.post(f"{url}/messages", headers=headers,
                          json={"question": "Условия кредита", "request_id": "delete-turn"}).status_code == 200
        assert owner.delete(url).status_code == 403
        assert other.delete(url, headers=csrf(other)).status_code == 404
        assert owner.get(url).status_code == 200
        assert owner.delete(url, headers=headers).status_code == 204
        assert owner.get(url).status_code == 404
        assert owner.delete(url, headers=headers).status_code == 404
        assert [row["id"] for row in owner.get("/api/real/chat/sessions").json()["sessions"]] == [kept]
        with connect_app() as connection:
            assert connection.execute("SELECT COUNT(*) FROM profile_chat_messages WHERE session_id = ?", (session,)).fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM profile_chat_requests WHERE session_id = ?", (session,)).fetchone()[0] == 0


def test_delete_during_generation_prevents_late_reply_from_restoring_chat(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app.chat_store import fail_turn, finish_turn, start_turn

    setup(monkeypatch)
    with TestClient(app) as client:
        register(client, "delete_pending")
        headers = csrf(client)
        session = client.post("/api/real/chat/sessions", headers=headers, json={}).json()["id"]
        key, _, lease = start_turn(session, "Вопрос до удаления", "pending-delete")
        assert client.delete(f"/api/real/chat/sessions/{session}", headers=headers).status_code == 204
        with pytest.raises(HTTPException) as late:
            finish_turn(session, key, {"answer": "Поздний ответ", "citations": []}, 1, lease)
        assert late.value.status_code == 409
        fail_turn(session, key, lease)
        with pytest.raises(HTTPException) as missing:
            start_turn(session, "Новый вопрос после удаления", "new-after-delete")
        assert missing.value.status_code == 404
        assert client.get("/api/real/chat/sessions").json()["sessions"] == []
