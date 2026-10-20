from fastapi.testclient import TestClient

from app.corpus.search import load_bank_documents, load_news, search_bank_analytics, search_news
from app.main import app


def test_rate_news_is_monetary_policy():
    news = {item.id: item for item in load_news()}
    rate = news["cbr-rate-14"]
    assert rate.event_type == "monetary_policy"
    assert rate.published_at == "2026-09-11"
    assert "14%" in rate.excerpt
    assert news["gold-august"].event_type == "precious_metals"
    assert news["brent-oil"].event_type == "commodities_energy"
    assert news["usd-rub"].event_type == "fx"
    assert news["inflation-august"].event_type == "inflation_macro"


def test_bank_quote_is_substring_and_search_finds_it():
    document = next(item for item in load_bank_documents() if item.doc_id == "key_rate")
    assert document.quote in document.text
    assert len(document.quote.split()) <= 25

    hits = search_bank_analytics("ключевая ставка")
    assert hits
    assert hits[0].id == "key_rate"

    news_hits = search_news("ключевая ставка 14%")
    assert news_hits[0].id == "cbr-rate-14"


def test_search_api():
    with TestClient(app) as client:
        response = client.get("/api/search", params={"q": "ключевая ставка"})
        assert response.status_code == 200
        body = response.json()
        assert body["bank"][0]["id"] == "key_rate"
        assert body["news"][0]["event_type"] == "monetary_policy"
