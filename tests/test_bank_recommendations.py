from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.news_store import bank_recommendations, recommendation_news, store_imported_news
from app.user_store import business_profile_for_owner, connect_app, create_user, init_app_db, save_business_profile


def _article(identifier="editorial-one"):
    return {"id": identifier, "title": "Решение Банка России по ключевой ставке",
            "text": "Банк России сохранил ключевую ставку на прежнем уровне. Условия кредитования не изменились.",
            "source": "Проверочный источник", "url": f"https://example.org/{identifier}",
            "published_at": date.today().isoformat(), "loaded_at": date.today().isoformat(), "sha256": "a" * 64}


def _csrf(client):
    return {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}


@pytest.fixture
def profile_id():
    user = create_user("editorial_company", "unused-test-hash")
    save_business_profile(user["id"], {"company_name": "ООО Проверка", "city": "Москва", "industry": "Торговля"}, "incomplete")
    return business_profile_for_owner(user["id"])["profile_id"]


def test_recommendation_is_individual_persistent_and_resettable(profile_id):
    store_imported_news("test", [_article(), _article("editorial-two")])
    with TestClient(app) as client:
        initial = client.get("/api/settings/news/recommendations", params={"profile_id": profile_id}).json()["items"]
        assert len(initial) == 2
        assert all(row["bank_recommendation"] == {"text": "", "page": None, "updated_at": None} for row in initial)
        saved = client.put("/api/settings/news/editorial-one/recommendation", headers=_csrf(client),
                           json={"profile_id": profile_id, "text": "  Сверьте график платежей.\nУточните условия договора.  ", "page": 3})
        assert saved.status_code == 200
        value = saved.json()["bank_recommendation"]
        assert value["text"] == "Сверьте график платежей.\nУточните условия договора."
        assert value["page"] == 3 and value["updated_at"]
        init_app_db()
        assert bank_recommendations(profile_id)["editorial-one"] == value
        assert "editorial-two" not in bank_recommendations(profile_id)
        assert next(row for row in recommendation_news(profile_id) if row["id"] == "editorial-one")["bank_recommendation"] == value
        cleared = client.put("/api/settings/news/editorial-one/recommendation", headers=_csrf(client),
                             json={"profile_id": profile_id, "text": " \n ", "page": 5})
        assert cleared.json()["bank_recommendation"] == {"text": "", "page": None, "updated_at": None}


def test_assessments_are_isolated_by_profile_and_visible_only_in_its_real_feed(monkeypatch):
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    store_imported_news("test", [_article()])
    with TestClient(app) as first, TestClient(app) as second:
        identities = []
        for reader, login in ((first, "bank_reader_one"), (second, "bank_reader_two")):
            registered = reader.post("/api/auth/register", headers=_csrf(reader),
                                     json={"login": login, "password": "correct-horse-battery"})
            assert registered.status_code == 201
            identities.append(business_profile_for_owner(registered.json()["id"])["profile_id"])
        for profile, text in zip(identities, ("Оценка для первой компании", "Оценка для второй компании")):
            saved = first.put("/api/settings/news/editorial-one/recommendation", headers=_csrf(first),
                              json={"profile_id": profile, "text": text})
            assert saved.status_code == 200
        for reader, expected in ((first, "Оценка для первой компании"), (second, "Оценка для второй компании")):
            feed = reader.get("/api/real/feed")
            assert feed.status_code == 200
            row = next(item for item in feed.json()["items"] if item["id"] == "editorial-one")
            assert row["bank_recommendation"]["text"] == expected
            assert row["report"] is None
        first.put("/api/settings/news/editorial-one/recommendation", headers=_csrf(first),
                  json={"profile_id": identities[0], "text": ""})
        assert bank_recommendations(identities[0]) == {}
        assert bank_recommendations(identities[1])["editorial-one"]["text"] == "Оценка для второй компании"



def test_update_requires_csrf_and_a_stored_news_id(profile_id):
    store_imported_news("test", [_article()])
    with TestClient(app) as client:
        assert client.put("/api/settings/news/editorial-one/recommendation", json={"profile_id": profile_id, "text": "x"}).status_code == 403
        assert client.put("/api/settings/news/missing/recommendation", headers=_csrf(client), json={"profile_id": profile_id, "text": "x"}).status_code == 404
        assert client.put("/api/settings/news/cbr-rate-14/recommendation", headers=_csrf(client), json={"profile_id": profile_id, "text": "x"}).status_code == 404
        assert "editorial-one" not in bank_recommendations(profile_id)


@pytest.mark.parametrize("payload", [{"text": "x" * 10001}, {"text": "x", "page": 0},
                                     {"text": "x", "page": 10001}, {"text": "x", "page": 1.5}])
def test_recommendation_validation(payload, profile_id):
    store_imported_news("test", [_article()])
    with TestClient(app) as client:
        assert client.put("/api/settings/news/editorial-one/recommendation", headers=_csrf(client), json={"profile_id": profile_id, **payload}).status_code == 422


def test_reimport_and_enrichment_preserve_editorial_recommendation(profile_id):
    article = _article()
    store_imported_news("test", [article])
    with TestClient(app) as client:
        client.put("/api/settings/news/editorial-one/recommendation", headers=_csrf(client),
                   json={"profile_id": profile_id, "text": "Рекомендация редактора", "page": 1})
    store_imported_news("test", [{**article, "id": "changed-guid", "text": article["text"] * 2, "sha256": "b" * 64}])
    assert bank_recommendations(profile_id)["editorial-one"]["text"] == "Рекомендация редактора"
    assert "changed-guid" not in bank_recommendations(profile_id)


def test_v12_migration_preserves_news_and_adds_empty_recommendations(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "legacy-v12.sqlite3"))
    with connect_app() as connection:
        connection.executescript("CREATE TABLE news_articles (id TEXT PRIMARY KEY, title TEXT); "
                                 "INSERT INTO news_articles VALUES ('existing', 'Исходная новость'); PRAGMA user_version=12;")
    init_app_db()
    init_app_db()
    assert bank_recommendations("unassigned-profile") == {}
    with connect_app() as connection:
        assert connection.execute("SELECT title FROM news_articles").fetchone()[0] == "Исходная новость"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14


def test_profile_search_data_and_required_selection(profile_id):
    with TestClient(app) as client:
        profiles = client.get("/api/settings/news/recommendation-profiles").json()["profiles"]
        assert len(profiles) == 1 and profiles[0]["id"] == profile_id
        assert profiles[0]["company_name"] == "ООО Проверка" and profiles[0]["city"] == "Москва"
        assert client.get("/api/settings/news/recommendations").status_code == 422
        assert client.get("/api/settings/news/recommendations?profile_id=missing").status_code == 404
        assert client.put("/api/settings/news/editorial-one/recommendation", headers=_csrf(client), json={"text": "x"}).status_code == 422
        assert client.put("/api/settings/news/editorial-one/recommendation", headers=_csrf(client), json={"profile_id": "missing", "text": "x"}).status_code == 404


def test_legacy_shared_text_is_preserved_but_never_assigned_to_a_profile(profile_id):
    store_imported_news("test", [_article()])
    with connect_app() as connection:
        connection.execute("UPDATE news_articles SET bank_recommendation = 'Прежний общий текст' WHERE id = 'editorial-one'")
    assert bank_recommendations(profile_id) == {}
    row = recommendation_news(profile_id)[0]
    assert row["bank_recommendation"]["text"] == ""
    assert row["legacy_bank_recommendation"]["text"] == "Прежний общий текст"
