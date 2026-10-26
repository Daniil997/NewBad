import json
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.bank_store import corpus_version, ensure_recommendations, import_corpus, search_passages, seed_path
from app.main import app
from app.corpus.search import load_bank_documents
from app.news_store import bank_recommendations, store_imported_news
from app.user_store import business_profile_for_owner, connect_app, create_session, create_user, init_app_db, save_business_profile


def material(**changes):
    return {"title": "Ключевая ставка и кредитование компаний", "published_at": date.today().isoformat(),
            "period": "25–31 августа", "notes": "Добавлено из текста обзора.",
            "text": "Ключевая ставка сохраняет жесткие условия кредитования компаний. "
                    "Высокая стоимость кредитов ограничивает инвестиции предприятий.", **changes}


@pytest.fixture
def client():
    user = create_user("material_editor", "synthetic-material-test-password")
    save_business_profile(user["id"], {"company_name": "Тестовая компания", "product_credit": "yes"}, "complete")
    with TestClient(app) as client:
        token, _ = create_session(user["id"])
        client.cookies.set("newbad_session", token)
        client.headers["X-CSRF-Token"] = client.get("/api/auth/csrf").json()["csrf_token"]
        yield client, business_profile_for_owner(user["id"])


def test_manual_material_persists_and_is_available_for_search_and_other_accounts(client):
    client, _ = client
    original_version = corpus_version()
    text = "\n\n".join([material()["text"], "Рост расходов на финансирование требует планирования ликвидности. " * 50]).strip()
    payload = material(title="  Ключевая ставка и кредитование компаний  ", text=text)
    result = client.post("/api/bank/analytics", json=payload)
    assert result.status_code == 201
    document_id = result.json()["id"]
    detail = client.get(f"/api/bank/analytics/{document_id}").json()
    assert detail["title"] == payload["title"].strip()
    assert detail["topic"] == "key_rate" and detail["type"] == "manual"
    assert detail["notes"] == payload["notes"] and detail["period"] == payload["period"]
    assert detail["pages"][0]["text"] == text
    assert len(detail["reviewed_passages"]) > 1
    assert any(row["document_id"] == document_id for row in search_passages("условия кредитования", limit=100))
    new_version = corpus_version()
    assert new_version != original_version
    assert import_corpus() is False
    init_app_db()
    assert corpus_version() == new_version
    other = create_user("material_reader", "synthetic-material-test-password")
    token, _ = create_session(other["id"])
    client.cookies.set("newbad_session", token)
    assert client.get(f"/api/bank/analytics/{document_id}").json()["pages"][0]["text"] == text
    assert client.get("/api/bank/analytics").json()["documents"] == 10
    with connect_app() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_manual_material_invalidates_negative_profile_matches(client):
    client, saved = client
    with connect_app() as connection:
        connection.execute("UPDATE bank_passages SET reviewed=0")
    store_imported_news("test", [{"id": "manual-rate-news", "title": "ЦБ сохранил ключевую ставку",
                                 "text": "Банк России сохранил ключевую ставку. Кредиты предприятий остаются дорогими.",
                                 "published_at": date.today().isoformat(), "loaded_at": date.today().isoformat(),
                                 "url": "https://example.org/manual-rate-news", "source": "Тестовый источник", "sha256": "manual-rate-news-hash"}])
    ensure_recommendations(saved["profile_id"])
    assert not bank_recommendations(saved["profile_id"])
    result = client.post("/api/bank/analytics", json=material())
    assert result.status_code == 201
    ensure_recommendations(saved["profile_id"])
    sources = bank_recommendations(saved["profile_id"])["manual-rate-news"]["sources"]
    assert sources[0]["document_id"] == result.json()["id"]
    assert sources[0]["text"] == material()["text"]


def test_seed_update_preserves_manual_materials_and_revision(client, monkeypatch, tmp_path):
    client, _ = client
    result = client.post("/api/bank/analytics", json=material())
    updated_seed = tmp_path / "updated-corpus.sqlite3"
    with sqlite3.connect(seed_path()) as source, sqlite3.connect(updated_seed) as target:
        source.backup(target)
        target.execute("UPDATE bank_corpus_meta SET value='test-new-seed' WHERE key='version'")
    monkeypatch.setenv("BANK_CORPUS_PATH", str(updated_seed))
    assert import_corpus() is True
    assert corpus_version() == "test-new-seed:manual-1"
    assert client.get(f"/api/bank/analytics/{result.json()['id']}").status_code == 200


def test_duplicate_material_is_atomic_and_does_not_change_caches(client):
    client, _ = client
    assert client.post("/api/bank/analytics", json=material()).status_code == 201
    version = corpus_version()
    result = client.post("/api/bank/analytics", json=material(text=material()["text"].replace(" ", "  ")))
    assert result.status_code == 409
    assert corpus_version() == version
    assert client.get("/api/bank/analytics").json()["documents"] == 10


@pytest.mark.parametrize("changes", [
    {"title": "   "}, {"text": " " * 100}, {"text": "Коротко"}, {"published_at": "2026-02-30"},
    {"published_at": "2026-9-02"}, {"published_at": "2026-09-02T12:00:00"}, {"topic": "made-up"},
    {"title": "x" * 241}, {"text": "x" * 100001},
])
def test_invalid_material_is_not_saved(client, changes):
    client, _ = client
    assert client.post("/api/bank/analytics", json=material(**changes)).status_code == 422
    assert client.get("/api/bank/analytics").json()["documents"] == 9


def test_creation_without_login_requires_valid_csrf():
    with TestClient(app) as client:
        assert client.post("/api/bank/analytics", json=material()).status_code == 403
        csrf = client.get("/api/auth/csrf").json()["csrf_token"]
        assert client.post("/api/bank/analytics", json=material()).status_code == 403
        assert client.post("/api/bank/analytics", json=material(), headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert client.post("/api/bank/analytics", json=material(), headers={"X-CSRF-Token": csrf}).status_code == 201
        assert "newbad_session" not in client.cookies
        with connect_app() as connection:
            assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_future_material_is_saved_but_not_used_before_publication(client):
    client, _ = client
    result = client.post("/api/bank/analytics", json=material(published_at=(date.today() + timedelta(days=1)).isoformat(), topic="inflation"))
    assert result.status_code == 201
    assert client.get(f"/api/bank/analytics/{result.json()['id']}").json()["topic"] == "inflation"
    assert not any(row["document_id"] == result.json()["id"] for row in search_passages("условия кредитования", limit=100))


def test_edit_and_delete_refresh_personal_matches_preserving_historical_links(client, monkeypatch):
    client, saved = client
    with connect_app() as connection:
        connection.execute("UPDATE bank_passages SET reviewed=0")
    document_id = client.post("/api/bank/analytics", json=material()).json()["id"]
    store_imported_news("test", [{"id": "edit-rate", "title": "ЦБ сохранил ключевую ставку",
                                 "text": "Банк России сохранил ключевую ставку. Кредиты предприятий остаются дорогими.",
                                 "published_at": date.today().isoformat(), "loaded_at": date.today().isoformat(),
                                 "url": "https://example.org/edit-rate", "source": "Тестовый источник", "sha256": "edit-rate"}])
    ensure_recommendations(saved["profile_id"])
    old_source = bank_recommendations(saved["profile_id"])["edit-rate"]["sources"][0]
    def report(_profile, _news, **context):
        return {"summary": "Ставка сохранена", "questions": [], "bank_sources": context["bank_sources"],
                "evidence_ids": ["news:edit-rate", old_source["evidence_id"]]}

    monkeypatch.setattr("app.main.analyze_profile_news", report)
    assert client.post("/api/real/news/edit-rate/report", json={}).status_code == 200
    old_version = corpus_version()
    replacement = material(title="Ключевая ставка: обновлённая оценка", text="Ключевая ставка остается высокой. Обслуживание кредитов повышает расходы компаний и требует контроля долга.")
    result = client.put(f"/api/bank/analytics/{document_id}", json={**replacement, "expected_revision": 0})
    assert result.status_code == 200
    detail = client.get(f"/api/bank/analytics/{document_id}").json()
    assert detail["revision"] == 1 and detail["edited"] == 1
    assert detail["pages"][0]["text"] == replacement["text"]
    assert corpus_version() != old_version
    ensure_recommendations(saved["profile_id"])
    source = bank_recommendations(saved["profile_id"])["edit-rate"]["sources"][0]
    assert source["text"] == replacement["text"] and source["passage_id"] != old_source["passage_id"]
    with connect_app() as connection:
        historical = connection.execute("SELECT text,active FROM bank_passages WHERE id=?", (old_source["passage_id"],)).fetchone()
        assert historical["text"] == material()["text"] and historical["active"] == 0
    assert not any(row["document_id"] == document_id for row in search_passages("инвестиции предприятий", limit=100))
    assert client.delete(f"/api/bank/analytics/{document_id}", params={"expected_revision": 1}).status_code == 204
    assert client.get(f"/api/bank/analytics/{document_id}").status_code == 404
    assert not any(row["document_id"] == document_id for row in search_passages("ключевая ставка", limit=100))
    assert not any(row.doc_id == document_id for row in load_bank_documents())
    ensure_recommendations(saved["profile_id"])
    assert not bank_recommendations(saved["profile_id"])
    init_app_db()
    assert client.get("/api/bank/analytics").json()["documents"] == 9
    with connect_app() as connection:
        historical_source = connection.execute("SELECT passage_id,source_json FROM profile_news_report_bank_sources").fetchone()
        assert historical_source["passage_id"] == old_source["passage_id"]
        assert json.loads(historical_source["source_json"])["text"] == material()["text"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_seed_edits_and_deletions_survive_updated_seed(client, monkeypatch, tmp_path):
    client, _ = client
    original = client.get("/api/bank/analytics/inflation").json()
    old_ids = [item["id"] for item in original["reviewed_passages"]]
    payload = material(title="Исправленное название инфляционного обзора", topic=original["topic"],
                       text="\n\n".join(page["text"] for page in original["pages"]),
                       reviewed_text="\n\n".join(item["text"] for item in original["reviewed_passages"]))
    assert client.put("/api/bank/analytics/inflation", json=payload).status_code == 200
    changed = client.get("/api/bank/analytics/inflation").json()
    assert [item["id"] for item in changed["reviewed_passages"]] == old_ids
    assert changed["pages"] == original["pages"]
    assert client.delete("/api/bank/analytics/gold").status_code == 204
    updated_seed = tmp_path / "new-seed.sqlite3"
    with sqlite3.connect(seed_path()) as source, sqlite3.connect(updated_seed) as target:
        source.backup(target)
        target.execute("UPDATE bank_corpus_meta SET value='next-seed' WHERE key='version'")
    monkeypatch.setenv("BANK_CORPUS_PATH", str(updated_seed))
    assert import_corpus() is True
    assert client.get("/api/bank/analytics/inflation").json()["title"] == payload["title"]
    assert client.get("/api/bank/analytics/gold").status_code == 404
    assert client.get("/api/bank/analytics").json()["documents"] == 8
    assert corpus_version() == "next-seed:manual-2"


def test_seed_full_text_edits_do_not_turn_ocr_into_reviewed_assessments(client):
    client, _ = client
    text = "Инфляция замедляется: исправленный текст обзора. Расходы компаний меняются вслед за ценами на потребительские товары."
    checked = "Инфляция замедляется, ценовое давление на расходы компаний снижается."
    assert client.put("/api/bank/analytics/inflation", json=material(text=text, topic="inflation", reviewed_text=checked)).status_code == 200
    detail = client.get("/api/bank/analytics/inflation").json()
    assert len(detail["pages"]) == 1 and detail["page_count"] == 1
    assert detail["pages"][0]["text"] == text
    assert [item["text"] for item in detail["reviewed_passages"]] == [checked]
    assert client.put("/api/bank/analytics/inflation", json=material(text=text, topic="inflation", reviewed_text="")).status_code == 200
    assert client.get("/api/bank/analytics/inflation").json()["reviewed_passages"] == []
    assert not any(row["document_id"] == "inflation" for row in search_passages("инфляция", reviewed_only=True, limit=100))


def test_conflicting_edit_or_delete_leaves_data_intact(client):
    client, _ = client
    document_id = client.post("/api/bank/analytics", json=material()).json()["id"]
    url = f"/api/bank/analytics/{document_id}"
    replacement = material(title="Новое название")
    assert client.put(url, json={**replacement, "expected_revision": 0}).status_code == 200
    version = corpus_version()
    assert client.put(url, json={**material(), "expected_revision": 0}).status_code == 409
    assert client.delete(url, params={"expected_revision": 0}).status_code == 409
    assert client.get(url).json()["title"] == replacement["title"]
    assert corpus_version() == version
    other = client.post("/api/bank/analytics", json=material(title="Другой материал")).json()["id"]
    assert client.put(f"/api/bank/analytics/{other}", json=replacement).status_code == 409
    assert client.put(url, json=material(text=" ")).status_code == 422
    assert client.put("/api/bank/analytics/missing", json=material()).status_code == 404
    assert client.delete("/api/bank/analytics/missing").status_code == 404


def test_edit_and_delete_without_login_require_csrf_and_deleted_content_can_be_added_again(client):
    client, _ = client
    document_id = client.post("/api/bank/analytics", json=material()).json()["id"]
    url = f"/api/bank/analytics/{document_id}"
    csrf = client.headers.pop("X-CSRF-Token")
    client.cookies.delete("newbad_session")
    assert client.put(url, json=material()).status_code == 403
    assert client.delete(url).status_code == 403
    assert client.put(url, json=material(), headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.delete(url, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.put(url, json=material(title="Обновлено без входа")).status_code == 200
    assert client.get(url).json()["title"] == "Обновлено без входа"
    assert client.delete(url).status_code == 204
    assert client.delete(url).status_code == 404
    recreated = client.post("/api/bank/analytics", json=material())
    assert recreated.status_code == 201 and recreated.json()["id"] != document_id
    assert client.get(url).status_code == 404
    assert client.put(f"/api/bank/analytics/{recreated.json()['id']}", json=material()).status_code == 200
    assert client.post("/api/bank/analytics", json=material()).status_code == 409


def test_version_15_database_is_migrated_without_losing_accounts_or_materials(client):
    client, _ = client
    document_id = client.post("/api/bank/analytics", json=material()).json()["id"]
    with connect_app() as connection:
        for table, fields in {"bank_documents": ("edited", "deleted", "revision", "content_hash"), "bank_passages": ("active", "position")}.items():
            for field in fields:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {field}")
        connection.execute("PRAGMA user_version=15")
    init_app_db()
    assert client.get(f"/api/bank/analytics/{document_id}").status_code == 200
    with connect_app() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17


@pytest.mark.parametrize("missing", [("position",), ("content_hash",), ("position", "content_hash"), ()])
def test_intermediate_version_16_is_repaired_without_overwriting_editorial_data(client, missing):
    client, saved = client
    document_id = client.post("/api/bank/analytics", json=material()).json()["id"]
    url = f"/api/bank/analytics/{document_id}"
    assert client.put(url, json=material(notes="Примечание, которое нужно сохранить")).status_code == 200
    assert client.delete("/api/bank/analytics/gold").status_code == 204
    version = corpus_version()
    with connect_app() as connection:
        for field in missing:
            table = "bank_passages" if field == "position" else "bank_documents"
            connection.execute(f"ALTER TABLE {table} DROP COLUMN {field}")
        connection.execute("PRAGMA user_version=16")
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("users", "sessions", "business_profiles", "bank_documents", "bank_pages", "bank_passages")}
    init_app_db()
    init_app_db()  # Repeated initialization must not replay ALTER TABLE or reset edits.
    assert client.get("/api/bank/analytics/bond_brief").status_code == 200
    detail = client.get(url).json()
    assert detail["notes"] == "Примечание, которое нужно сохранить" and detail["revision"] == 1
    assert detail["pages"][0]["text"] == material()["text"]
    assert client.get("/api/bank/analytics/gold").status_code == 404
    assert corpus_version() == version
    with connect_app() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
        assert counts == {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in counts}
        assert connection.execute("SELECT owner_id FROM business_profiles WHERE id=?", (saved["profile_id"],)).fetchone()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert client.put(url, json=material(title="После исправления миграции")).status_code == 200
    assert client.delete(url).status_code == 204
