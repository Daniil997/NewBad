from fastapi.testclient import TestClient

from app.corpus.search import load_news
from app.main import app


def _why(card: dict) -> str:
    return " ".join(item["text"] for item in card["why_for_company"]["claims"])


def test_chat_and_steps_follow_the_card(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as client:
        borrower = client.post(
            "/api/clients/U-BORROWER/events/cbr-rate-14/chat",
            json={"question": "Почему это важно компании?"},
        )
        saver = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Почему это важно компании?"},
        )
        bank = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Что говорит банк?"},
        )
        empty = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "   "},
        )
        steps = client.get("/api/clients/U-SAVER/events/cbr-rate-14/steps")
        settings = client.get("/api/settings")

    assert borrower.status_code == 200
    assert "стоимость заёмных денег" in borrower.json()["answer"]
    assert saver.status_code == 200
    assert "доходность размещения" in saver.json()["answer"]
    assert saver.json()["mode"] == "mock"
    assert "сохранение ключевой ставки" in bank.json()["answer"]
    assert empty.status_code == 400
    assert "PLAN" in steps.json()["steps"]
    assert "FINALIZE" in steps.json()["steps"]
    assert settings.json()["mode"] == "mock"
    assert settings.json()["weights_from_browser"] is False
    assert settings.json()["active_generation"] == "mock"


def test_pasted_news_builds_card_without_joining_the_feed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    payload = {
        "title": "Ключевая ставка сохранена",
        "text": "Совет директоров сохранил ключевую ставку на уровне 14% годовых.",
        "url": "https://example.test/rate",
    }
    with TestClient(app) as client:
        created = client.post("/api/clients/U-BORROWER/news", json=payload)
        blank = client.post("/api/clients/U-BORROWER/news", json={"title": " ", "text": " "})
        feed = client.get("/api/clients/U-BORROWER/feed")

    assert created.status_code == 200
    card = created.json()
    assert card["event_id"].startswith("pasted-")
    assert "стоимость заёмных денег" in _why(card)
    assert card["sources"][0]["url"] == "https://example.test/rate"
    assert blank.status_code == 400
    ids = [item["news_id"] for item in feed.json()["top"] + feed.json()["less_relevant"]]
    assert card["event_id"] not in ids
    assert card["event_id"] not in {item.id for item in load_news()}
