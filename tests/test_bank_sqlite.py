import json
import sqlite3
from datetime import date

from fastapi.testclient import TestClient

from app.agent.generate import analyze_profile_news
from app.bank_store import (
    bank_evidence, corpus_status, ensure_recommendations, import_corpus, search_passages, seed_path,
)
from app.corpus.search import NewsItem, load_news
from app.llm.router import Route, TierResult
from app.main import _profile_chat_evidence, app
from app.news_store import bank_recommendations, save_bank_recommendation, store_imported_news
from app.user_store import (
    business_profile_for_owner, connect_app, create_session, create_user, init_app_db, save_business_profile,
)


def company(login, **fields):
    user = create_user(login, "not-a-real-password")
    save_business_profile(user["id"], {"company_name": login, **fields}, "complete")
    return user, business_profile_for_owner(user["id"])


def article():
    return {"id": "rate-change", "title": "ЦБ сохранил ключевую ставку",
            "text": "Банк России сохранил ключевую ставку. Условия кредитования предприятий остаются жесткими.",
            "published_at": date.today().isoformat(), "loaded_at": date.today().isoformat(),
            "url": "https://example.org/rate-change", "source": "Новости", "sha256": "a" * 64,
            "event_type": "monetary_policy", "event_title": "Ключевая ставка"}


def test_complete_corpus_survives_fresh_install_without_pdf_or_ocr(monkeypatch):
    monkeypatch.setattr("app.profile.sources.bank_pdf_dir", lambda: (_ for _ in ()).throw(AssertionError("PDF read")))
    init_app_db()
    assert corpus_status()["documents"] == 9
    assert corpus_status()["pages"] == 64
    assert corpus_status()["reviewed_passages"] == 17
    assert import_corpus() is False
    with connect_app() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT published_at FROM bank_documents WHERE id='consumption'").fetchone()[0] == "2026-08-05"
        assert "41 сентября" in connection.execute("SELECT raw_text FROM bank_pages WHERE id='key_rate:1'").fetchone()[0]
        assert connection.execute("SELECT SUM(length(raw_text)) FROM bank_pages").fetchone()[0] > 150000
    with sqlite3.connect(seed_path()) as seed:
        assert {row[0] for row in seed.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "bank_documents", "bank_pages", "bank_passages", "bank_corpus_meta",
        }


def test_sources_are_individual_and_manual_edits_win():
    _, borrower = company("borrower", product_credit="yes")
    _, unrelated = company("unrelated", industry="Разработка программного обеспечения")
    store_imported_news("test", [article()])
    news = load_news()
    ensure_recommendations(borrower["profile_id"], news)
    ensure_recommendations(unrelated["profile_id"], news)
    recommended = bank_recommendations(borrower["profile_id"])["rate-change"]
    assert recommended["origin"] == "corpus" and recommended["sources"]
    assert "кредитные продукты" in recommended["match_reason"]
    assert bank_recommendations(unrelated["profile_id"]) == {}
    with connect_app() as connection:
        assert connection.execute("SELECT COUNT(*) FROM profile_bank_sources").fetchone()[0] == len(recommended["sources"])
    save_bank_recommendation(borrower["profile_id"], "rate-change", "Ручная оценка редактора", None)
    ensure_recommendations(borrower["profile_id"], news)
    assert bank_recommendations(borrower["profile_id"])["rate-change"]["text"] == "Ручная оценка редактора"
    with connect_app() as connection:
        assert connection.execute("SELECT COUNT(*) FROM profile_bank_sources").fetchone()[0] == 0


def test_profile_changes_remove_old_automatic_links_and_reimport_is_idempotent():
    user, saved = company("changing", product_credit="yes")
    store_imported_news("test", [article()])
    ensure_recommendations(saved["profile_id"])
    assert bank_recommendations(saved["profile_id"])
    save_business_profile(user["id"], {"company_name": "changing", "product_credit": "no"}, "complete")
    ensure_recommendations(saved["profile_id"])
    assert bank_recommendations(saved["profile_id"]) == {}
    init_app_db()
    assert corpus_status()["pages"] == 64


def test_date_boundaries_do_not_leak_future_or_historical_forecasts():
    assert search_passages("ключевая ставка", as_of=date(2020, 1, 1)) == []
    assert all(row["document_id"] != "key_rate" for row in search_passages(
        "ключевая ставка", as_of=date(2026, 9, 10), limit=100, reviewed_only=True,
    ))
    old = search_passages("сталь экспорт", reviewed_only=True, limit=100)
    assert any(row["document_id"] == "steels" and row["stale"] for row in old)
    news = NewsItem(id="steel", title="Российская сталь и экспорт металлов", excerpt="Растут цены на сталь.",
                    published_at=date.today().isoformat(), url="https://example.org/steel", event_type="metals", event_title="Металлы")
    sources, _ = bank_evidence({"industry_tags": ["manufacturing"]}, news)
    assert not any(source["document_id"] == "steels" for source in sources)


def test_model_and_chat_receive_bank_sources_with_separate_attribution(monkeypatch):
    _, saved = company("analysis", product_credit="yes")
    store_imported_news("test", [article()])
    news = load_news()[0]
    calls = []

    def model(messages, **options):
        evidence = json.loads(messages[1]["content"])["evidence"]
        bank = next(row for row in evidence if row.get("kind") == "bank")
        calls.append(bank)
        reply = {
            "summary": "Банк России сохранил ключевую ставку и жесткие условия кредитования предприятий.",
            "relevance": "Оценка AI: с учетом позиции банка условия привлечения денег могут оставаться жесткими для заемщика.",
            "risks": "Оценка AI: если компании нужно рефинансирование, стоимость новых кредитов может ограничивать инвестиции.",
            "opportunities": "Оценка AI: сопоставление сроков кредитов с денежным потоком позволит заранее оценить потребность в ликвидности.",
            "questions": [], "evidence_ids": ["company-profile", f"news:{news.id}", bank["evidence_id"]],
        }
        return TierResult(route=Route(mode="local_only", provider="local", model="test"),
                          text=json.dumps(reply, ensure_ascii=False), tokens=1)

    monkeypatch.setattr("app.agent.generate.complete_tier", model)
    report = analyze_profile_news(saved["profile"], news)
    assert report["origin"] == "model"
    assert report["bank_sources"] and calls
    assert all(not source["url"] for source in report["bank_sources"])
    evidence = _profile_chat_evidence(saved, news.id, "Что говорит банк о ставке?")
    assert any(row.get("kind") == "bank" for row in evidence)
    assert any(row.get("focus") for row in evidence)


def test_corpus_api_and_profile_feed_expose_sqlite_content_without_files():
    user, _ = company("reader", product_credit="yes")
    store_imported_news("test", [article()])
    with TestClient(app) as client:
        assert client.get("/api/bank/analytics").status_code == 200
        corpus = client.get("/api/bank/analytics", params={"q": "ставка"}).json()
        assert corpus["documents"] == 9 and corpus["matches"]
        document = client.get("/api/bank/analytics/inflation").json()
        assert len(document["pages"]) == 3 and document["reviewed_passages"]
        assert client.get("/api/bank/analytics/missing").status_code == 404
        assert client.get("/api/real/feed").status_code == 401
        assert client.get("/api/real/profile").status_code == 401
        token, _ = create_session(user["id"])
        client.cookies.set("newbad_session", token)
        feed = client.get("/api/real/feed").json()
        row = next(item for item in feed["items"] if item["id"] == "rate-change")
        assert row["bank_recommendation"]["origin"] == "corpus"
        assert row["bank_recommendation"]["sources"]


def test_report_sources_persist_and_cached_report_is_returned_in_feed(monkeypatch):
    user, saved = company("report_reader", product_credit="yes")
    store_imported_news("test", [article()])
    news = load_news()[0]
    sources, _ = bank_evidence(saved["profile"], news)
    calls = []

    def report(_profile, _news, **_context):
        calls.append(_news.id)
        return {"summary": "Ставка сохранена", "relevance": "Оценка влияния на заемщика",
                "risks": "Стоимость финансирования", "opportunities": "Планирование ликвидности",
                "questions": [], "origin": "model", "bank_sources": sources[:1],
                "evidence_ids": ["company-profile", f"news:{news.id}", sources[0]["evidence_id"]]}

    monkeypatch.setattr("app.main.analyze_profile_news", report)
    with TestClient(app) as client:
        token, _ = create_session(user["id"])
        client.cookies.set("newbad_session", token)
        csrf = {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}
        first = client.post(f"/api/real/news/{news.id}/report", json={}, headers=csrf)
        assert first.status_code == 200
        assert any(item["kind"] == "bank" for item in first.json()["citations"])
        cached = client.post(f"/api/real/news/{news.id}/report", json={}, headers=csrf).json()
        assert cached["cached"] is True and len(calls) == 1
        row = next(item for item in client.get("/api/real/feed").json()["items"] if item["id"] == news.id)
        assert row["report"]["bank_sources"] == sources[:1]
        with connect_app() as connection:
            assert connection.execute("SELECT COUNT(*) FROM profile_news_report_bank_sources").fetchone()[0] == 1
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_legacy_report_markers_are_hidden_in_cached_response_and_feed_without_rewriting_sources(monkeypatch):
    user, saved = company("report_markers", product_credit="yes")
    store_imported_news("test", [article()])
    news = load_news()[0]
    sources, _ = bank_evidence(saved["profile"], news)
    source = sources[0]
    marker = source["evidence_id"]
    report = {"summary": "Ставка сохранена", "relevance": "Возможное влияние на заемщика",
              "risks": f"Рост расходов по оценке банка ({marker}).", "opportunities": "Планирование ликвидности",
              "questions": [], "bank_sources": [source], "evidence_ids": [f"news:{news.id}", marker]}
    monkeypatch.setattr("app.main.analyze_profile_news", lambda *_args, **_kwargs: dict(report))
    with TestClient(app) as client:
        token, _ = create_session(user["id"])
        client.cookies.set("newbad_session", token)
        csrf = {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}
        first = client.post(f"/api/real/news/{news.id}/report", json={}, headers=csrf)
        assert first.status_code == 200 and marker not in first.json()["risks"]
        with connect_app() as connection:
            row = connection.execute("SELECT id,report_json FROM profile_news_reports").fetchone()
            legacy = {**json.loads(row["report_json"]), "risks": report["risks"]}
            connection.execute("UPDATE profile_news_reports SET report_json=? WHERE id=?", (json.dumps(legacy), row["id"]))
        cached = client.post(f"/api/real/news/{news.id}/report", json={}, headers=csrf).json()
        assert cached["cached"] is True and cached["risks"] == "Рост расходов по оценке банка."
        feed_item = next(item for item in client.get("/api/real/feed").json()["items"] if item["id"] == news.id)
        assert feed_item["report"]["risks"] == cached["risks"]
        assert cached["bank_sources"] == [source] and marker in cached["evidence_ids"]
        assert any(item["kind"] == "bank" and item["title"] == source["title"] for item in cached["citations"])
        with connect_app() as connection:
            assert marker in json.loads(connection.execute("SELECT report_json FROM profile_news_reports").fetchone()[0])["risks"]
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
