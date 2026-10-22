import pytest
from fastapi.testclient import TestClient

from app.agent.chat import answer_from_card
from app.agent.loop import build_card
from app.corpus.search import CorpusError
from app.main import app


def test_question_stays_on_its_card(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    card = build_card("U-SAVER", "cbr-rate-14")
    assert card is not None
    with pytest.raises(ValueError, match="не относится"):
        answer_from_card(card, "Что говорит банк?", "U-BORROWER", card.event_id)

    with TestClient(app) as client:
        lost = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Повтори, что ты сказал"},
        )
        borrower = client.post(
            "/api/clients/U-BORROWER/events/cbr-rate-14/chat",
            json={"question": "Почему это важно компании?"},
        )
        saver_lost = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Повтори, что ты сказал"},
        )
        profile = client.post("/api/clients/U-BORROWER/events/cbr-rate-14/card")
        facts = client.post(
            "/api/clients/U-BORROWER/events/cbr-rate-14/chat",
            json={"question": "Какие факты профиля использованы?"},
        )
        bank = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Что говорит банк?"},
        )
        repeated = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Повтори, что ты сказал"},
        )

    assert lost.status_code == 200
    assert lost.json()["answer"] == "Контекст диалога по этой карточке пуст."
    assert "стоимость заёмных денег" in borrower.json()["answer"]
    assert "стоимость заёмных денег" not in saver_lost.json()["answer"]
    fact = profile.json()["profile_facts"][0]
    assert fact["label"] in facts.json()["answer"]
    assert fact["value"] in facts.json()["answer"]
    body = bank.json()
    assert body["mode"] == "mock"
    assert body["citations"]
    assert body["citations"][0]["quote"]
    assert body["citations"][0]["quote"] in body["answer"]
    assert "Источник:" in body["answer"]
    assert repeated.json()["answer"] == body["answer"]


def test_extra_search_is_limited(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    calls: list[str] = []

    def fake(name, arguments):
        calls.append(name)
        if name == "boom":
            raise CorpusError("index", "Индекс банка не построен")
        return {
            "tool": name,
            "status": "ok",
            "evidence_id": "oil:2",
            "source": "bank_analytics",
            "message": "",
            "items": [
                {
                    "evidence_id": "oil:2",
                    "id": "oil",
                    "title": "Нефть",
                    "page": 2,
                    "quote": "Нефть на тестовом стенде подешевела.",
                    "url": "",
                }
            ],
        }

    monkeypatch.setattr("app.agent.chat.run_tool", fake)
    question = "Найди другие материалы банка про нефть"
    with TestClient(app) as client:
        covered = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Что говорит банк?"},
        )
        first = client.post("/api/clients/U-SAVER/events/cbr-rate-14/chat", json={"question": question})
        second = client.post("/api/clients/U-SAVER/events/cbr-rate-14/chat", json={"question": question})
        third = client.post("/api/clients/U-SAVER/events/cbr-rate-14/chat", json={"question": question})

    assert covered.status_code == 200
    assert calls == ["search_bank_analytics", "search_bank_analytics"]
    assert "Нефть на тестовом стенде подешевела." in first.json()["answer"]
    assert first.json()["citations"][0]["page"] == 2
    assert "Источник:" in first.json()["answer"]
    assert second.json()["answer"] == first.json()["answer"]
    assert "уже выполнен" in third.json()["answer"]


def test_search_tool_error_stays_on_the_card(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def broken(_name, _arguments):
        raise CorpusError("load", "Не читается паспорт")

    monkeypatch.setattr("app.agent.chat.run_tool", broken)
    with TestClient(app) as client:
        response = client.post(
            "/api/clients/U-BORROWER/events/cbr-rate-14/chat",
            json={"question": "Найди другие материалы банка про нефть"},
        )

    assert response.status_code == 200
    assert response.json()["mode"] == "mock"
    assert "Инструмент поиска не ответил" in response.json()["answer"]
    assert response.json()["citations"] == []
