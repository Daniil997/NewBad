import httpx
from fastapi.testclient import TestClient

from app.agent.draft import draft_card
from app.agent.loop import _gather, _plan
from app.agent.loop_context import AgentContext
from app.agent.verify import verify_card
from app.corpus.search import CorpusError, load_bank_documents
from app.main import app


def test_select_company_profile_and_personalized_feed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    with TestClient(app) as client:
        listing = client.get("/api/clients")
        borrower = client.get("/api/clients/U-BORROWER")
        saver = client.get("/api/clients/U-SAVER")
        borrower_feed = client.get("/api/clients/U-BORROWER/feed")
        saver_feed = client.get("/api/clients/U-SAVER/feed")

    assert listing.status_code == 200
    ids = {item["client_id"] for item in listing.json()}
    assert {"U-BORROWER", "U-SAVER", "U-PEER"} <= ids
    assert borrower.status_code == 200
    assert borrower.json()["money_role"] == "borrower"
    assert borrower.json()["exposures"]
    assert borrower.json()["missing_data"]
    assert saver.json()["money_role"] == "saver"
    assert saver.json()["exposures"]
    borrower_top = borrower_feed.json()["top"]
    saver_top = saver_feed.json()["top"]
    borrower_rate = next(item for item in borrower_top if item["news_id"] == "cbr-rate-14")
    saver_items = [*saver_top, *saver_feed.json()["less_relevant"]]
    saver_rate = next(item for item in saver_items if item["news_id"] == "cbr-rate-14")
    assert "Заёмное финансирование" in borrower_rate["reason"]
    assert "Депозиты" in saver_rate["reason"]
    assert borrower_rate["score"] != saver_rate["score"]


def test_card_cites_bank_page_and_news(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    with TestClient(app) as client:
        response = client.post("/api/clients/U-BORROWER/events/cbr-rate-14/card")

    assert response.status_code == 200
    card = response.json()
    news = next(item for item in card["sources"] if item["kind"] == "news")
    bank = next(item for item in card["sources"] if item["kind"] == "bank_analytics")
    assert news["url"].startswith("https://")
    assert bank["page"] >= 1
    assert bank["quote"]
    pages = [
        item
        for item in load_bank_documents()
        if item.doc_id == bank["id"] and item.page == bank["page"] and bank["quote"] in item.text
    ]
    assert pages
    assert card["bank_view"]["quote"] == bank["quote"]
    assert card["bank_view"]["page"] == bank["page"]


def test_missing_profile_data_is_named(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    with TestClient(app) as client:
        profile = client.get("/api/clients/U-BORROWER")
        card = client.post("/api/clients/U-BORROWER/events/cbr-rate-14/card")
        chat = client.post(
            "/api/clients/U-BORROWER/events/cbr-rate-14/chat",
            json={"question": "Каких данных не хватает?"},
        )

    assert "REGION" in profile.json()["missing_data"]
    missing = card.json()["missing_data"]
    assert any(item.startswith("Неизвестно:") for item in missing)
    assert "Неизвестно: Регион" in missing
    assert chat.status_code == 200
    assert "Неизвестно: Регион" in chat.json()["answer"]


def test_validator_rejects_invented_number_and_quote(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctx = AgentContext(client_id="U-BORROWER", event_id="cbr-rate-14")
    _plan(ctx)
    _gather(ctx)
    card = draft_card(ctx)
    assert verify_card(card, ctx) == []
    card.bank_view.quote = "выдуманная позиция которой нет в тексте"
    card.bank_position[0] = card.bank_position[0].model_copy(update={"text": card.bank_view.quote})
    card.what_happened.claims[0] = card.what_happened.claims[0].model_copy(
        update={"text": "Регулятор поднял ставку до 1234567 процентов."}
    )
    issues = verify_card(card, ctx)
    assert "quote" in issues
    assert "number:1234567" in issues


def test_user_asks_a_follow_up_about_the_card(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    with TestClient(app) as client:
        first = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Что говорит банк?"},
        )
        second = client.post(
            "/api/clients/U-SAVER/events/cbr-rate-14/chat",
            json={"question": "Что ты сказал?"},
        )

    assert first.status_code == 200
    body = first.json()
    assert body["mode"] == "mock"
    assert body["citations"]
    assert body["citations"][0]["page"] >= 1
    assert body["citations"][0]["quote"] in body["answer"]
    assert "Источник:" in body["answer"]
    assert second.status_code == 200
    assert second.json()["answer"] == body["answer"]


def test_source_and_llm_failures_stay_visible(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("ALLOW_CLOUD", "false")

    def broken_feed(_client_id: str):
        raise CorpusError("load", "Не читается новость cbr-rate-14.json: JSONDecodeError")

    monkeypatch.setattr("app.main.build_feed", broken_feed)
    with TestClient(app) as client:
        clients = client.get("/api/clients")
        feed = client.get("/api/clients/U-BORROWER/feed")
        short = client.post(
            "/api/clients/U-BORROWER/news",
            json={"title": "Коротко", "text": "Мало"},
        )
    assert clients.status_code == 200
    assert feed.status_code == 503
    assert feed.json()["detail"] == "Не читается новость cbr-rate-14.json: JSONDecodeError"
    assert "\\" not in feed.json()["detail"]
    assert short.status_code == 400
    assert short.json()["detail"] == "Текст новости слишком короткий"

    def broken_bank():
        raise CorpusError("load", "Не читается паспорт key_rate.json: JSONDecodeError")

    monkeypatch.setattr("app.main.load_bank_documents", broken_bank)
    with TestClient(app) as client:
        status = client.get("/api/status")
    bank = next(item for item in status.json()["indexes"] if item["name"] == "bank")
    assert status.json()["status"] == "degraded"
    assert bank["ready"] is False
    assert bank["error"] == "Не читается паспорт key_rate.json: JSONDecodeError"
    assert "\\" not in bank["error"]

    monkeypatch.setenv("LLM_MODE", "local_only")
    monkeypatch.setattr("app.llm.router.server_reachable", lambda: False)

    def down(_url: str, _payload: dict, timeout: float, headers: dict | None = None) -> dict:
        del timeout, headers
        raise httpx.ConnectError("нет сервера")

    monkeypatch.setattr("app.llm.client.post_chat", down)

    def search_down(*_args, **_kwargs):
        raise CorpusError("index", "Индекс банка не построен: ValueError")

    monkeypatch.setattr("app.agent.chat.run_tool", search_down)
    with TestClient(app) as client:
        card = client.post("/api/clients/U-BORROWER/events/cbr-rate-14/card")
        settings = client.get("/api/settings")
        chat = client.post(
            "/api/clients/U-BORROWER/events/cbr-rate-14/chat",
            json={"question": "Найди прогноз урожайности хлопка"},
        )
    assert card.status_code == 200
    trace = card.json()["llm_trace"]
    assert trace["origin"] == "template"
    assert trace["mode"] == "mock"
    assert trace["model"] == "mock-model"
    assert "ConnectError" not in card.text
    assert settings.json()["active_generation"] == "mock"
    assert chat.status_code == 200
    assert chat.json()["answer"] == "Инструмент поиска не ответил. Ответ остаётся по открытой карточке."
    assert chat.json()["mode"] == "mock"
