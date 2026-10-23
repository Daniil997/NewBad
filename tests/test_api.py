import os

from fastapi.testclient import TestClient

from app.main import app

_SECRET = "sk-test-secret-value"


def test_company_list_and_profile():
    with TestClient(app) as client:
        listing = client.get("/api/clients")
        profile = client.get("/api/clients/U-BORROWER")
        broken = client.get("/api/clients/нет такой")

    assert listing.status_code == 200
    row = next(item for item in listing.json() if item["client_id"] == "U-BORROWER")
    assert row["money_role"] == "borrower"
    assert "rate_borrower" in row["exposure_ids"]
    assert "pay_amt" not in row

    body = profile.json()
    assert body["client_id"] == "U-BORROWER"
    assert body["exposures"]
    assert body["missing_data"]
    assert broken.status_code == 400
    assert broken.json()["detail"] == "Некорректный идентификатор компании"


def test_feed_card_sources_chat_and_paste(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as client:
        feed = client.get("/api/clients/U-SAVER/feed")
        card = client.post("/api/clients/U-SAVER/events/cbr-rate-14/card")
        chat = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Что говорит банк?"},
        )
        pasted = client.post(
            "/api/clients/U-BORROWER/news",
            json={
                "title": "Ключевая ставка сохранена",
                "text": "Совет директоров сохранил ключевую ставку на уровне 14% годовых.",
            },
        )
        missing_chat = client.post(
            "/api/clients/missing/events/cbr-rate-14/chat",
            json={"question": "Что говорит банк?"},
        )
        bad_body = client.post("/api/clients/U-SAVER/events/cbr-rate-14/chat", json={})
        long_query = client.get("/api/search", params={"q": "а" * 301})

    assert feed.status_code == 200
    assert any(item["news_id"] == "cbr-rate-14" for item in feed.json()["top"])
    assert card.status_code == 200
    sources = card.json()["sources"]
    assert any(item["kind"] == "news" and item["id"] == "cbr-rate-14" for item in sources)
    bank = next(item for item in sources if item["kind"] == "bank_analytics")
    assert bank["page"] >= 1
    assert bank["quote"]
    assert chat.status_code == 200
    assert chat.json()["source_ids"]
    assert chat.json()["mode"] == "mock"
    assert pasted.status_code == 200
    assert pasted.json()["event_id"].startswith("pasted-")
    assert missing_chat.status_code == 404
    assert bad_body.status_code == 422
    assert bad_body.json()["detail"] == "Некорректные параметры"
    assert "body.question" in bad_body.json()["fields"]
    assert _SECRET not in bad_body.text
    assert long_query.status_code == 400


def test_status_hides_the_key_and_reports_indexes(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    monkeypatch.setenv("CASE_LLM_API_KEY", _SECRET)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm"]["mode"] == "mock"
    assert body["llm"]["provider"] == "mock"
    assert body["llm"]["model"] == "mock-model"
    assert body["llm"]["key_configured"] is True
    assert body["llm"]["allow_cloud"] is False
    indexes = {item["name"]: item for item in body["indexes"]}
    assert indexes["news"]["ready"] is True
    assert indexes["news"]["count"] >= 10
    assert indexes["bank"]["ready"] is True
    assert indexes["bank"]["count"] >= 1
    assert "llm_calls" in body["journal"]
    text = response.text
    assert _SECRET not in text
    assert "Authorization" not in text
    assert "CASE_LLM" not in text


def test_runtime_settings_switch_without_touching_the_key(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    monkeypatch.setenv("LLM_PLACEMENT", "auto")
    monkeypatch.setenv("CASE_LLM_API_KEY", _SECRET)
    with TestClient(app) as client:
        changed = client.post(
            "/api/settings",
            json={"mode": "replay", "allow_cloud": False, "placement": "ram"},
        )
        broken = client.post(
            "/api/settings",
            json={"mode": "gpt-4", "allow_cloud": True, "placement": "auto"},
        )
    assert changed.status_code == 200
    assert changed.json()["mode"] == "replay"
    assert changed.json()["allow_cloud"] is False
    assert os.environ["LLM_PLACEMENT"] == "ram"
    assert _SECRET not in changed.text
    assert broken.status_code == 400
    assert broken.json()["detail"] == "Некорректный общий режим модели"
    assert os.environ["LLM_MODE"] == "replay"


def test_feedback_is_persisted_and_metrics_are_aggregated(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as client:
        positive = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/feedback", json={"helpful": True}
        )
        negative = client.post(
            "/api/clients/U-BORROWER/events/cbr-rate-14/feedback", json={"helpful": False}
        )
        invalid = client.post("/api/clients/U-SAVER/events/no-such-event/feedback", json={"helpful": True})
        metrics = client.get("/api/metrics/feedback")

    assert positive.json() == {"status": "saved"}
    assert negative.json() == {"status": "saved"}
    assert invalid.status_code == 404
    assert metrics.json() == {"responses": 2, "helpful": 1, "not_helpful": 1, "helpful_share": 0.5}
    assert "U-SAVER" not in metrics.text
