from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.news_store import imported_news, prune_stored_news, store_imported_news
from app.user_store import init_app_db


def test_news_developer_settings_persist_and_validate(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    with TestClient(app) as client:
        defaults = client.get("/api/settings")
        saved = client.post("/api/settings", json={
            "news_lookback_days": 7, "news_retention_days": 45,
            "news_max_items": 120, "news_collection_interval_hours": 6,
        })
        loaded = client.get("/api/settings")
        invalid = client.post("/api/settings", json={
            "news_lookback_days": 90, "news_retention_days": 45,
            "news_max_items": 120, "news_collection_interval_hours": 6,
        })
    assert defaults.status_code == 200
    assert defaults.json()["news_lookback_days"] == 3
    assert saved.status_code == 200
    assert loaded.json()["news_retention_days"] == 45
    assert loaded.json()["news_max_items"] == 120
    assert loaded.json()["news_collection_interval_hours"] == 6
    assert invalid.status_code == 422


def test_task_model_settings_round_trip_and_reject_unknown_models(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "task-routes.sqlite3"))
    routes = {
        "real": {"profile_site": "local_gemma", "news_report": "cloud_gemma", "news_selection": "local_qwen", "chat": "mock"},
        "demo": {"news_card": "mock", "news_card_fact": "local_gemma", "news_card_copy": "cloud_qwen", "chat": "local_qwen"},
    }
    with TestClient(app) as client:
        saved = client.post("/api/settings", json={
            "task_routes": routes,
        })
        loaded = client.get("/api/settings")
        invalid = client.post("/api/settings", json={
            "task_routes": {"real": {"news_report": "gpt-4"}},
        })
    assert saved.status_code == 200
    assert saved.json()["task_routes"] == routes
    assert loaded.json()["task_routes"] == routes
    assert invalid.status_code == 422


def test_task_route_save_does_not_require_or_reset_legacy_global_model_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "task-routes-only.sqlite3"))
    monkeypatch.setenv("LLM_MODE", "local_dual")
    monkeypatch.setenv("ALLOW_CLOUD", "true")
    monkeypatch.setenv("LLM_PLACEMENT", "gpu")
    routes = {
        "real": {"profile_site": "local_gemma", "news_report": "mock", "news_selection": "local_gemma", "chat": "mock"},
        "demo": {"news_card": "local_qwen", "news_card_fact": "local_qwen", "news_card_copy": "local_gemma", "chat": "mock"},
    }

    with TestClient(app) as client:
        saved = client.post("/api/settings", json={"task_routes": routes})
        loaded = client.get("/api/settings")

    assert saved.status_code == 200
    assert loaded.status_code == 200
    assert loaded.json()["task_routes"] == routes
    assert __import__("os").environ["LLM_MODE"] == "local_dual"
    assert __import__("os").environ["ALLOW_CLOUD"] == "true"
    assert __import__("os").environ["LLM_PLACEMENT"] == "gpu"


def test_demo_reset_clears_runtime_data_but_keeps_static_fixtures(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.db import connect, init_db

    init_db()
    with connect() as connection:
        connection.execute(
            "INSERT INTO chat_turns (client_id, event_id, role, text, created_at) VALUES (?, ?, ?, ?, ?)",
            ("U-BORROWER", "cbr-rate-14", "user", "test question", "2026-10-24T10:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO user_feedback (client_id, event_id, helpful, created_at) VALUES (?, ?, ?, ?)",
            ("U-BORROWER", "cbr-rate-14", 1, "2026-10-24T10:00:00+00:00"),
        )
    with TestClient(app) as client:
        response = client.post("/api/settings/demo/reset")
        static_profile = client.get("/api/clients/U-BORROWER")
    assert response.status_code == 200
    assert static_profile.status_code == 200
    assert response.json()["removed"] >= 2
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM chat_turns").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM user_feedback").fetchone()[0] == 0


def test_manual_news_collection_reports_per_source_results(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setattr("app.main.import_news_catalog", lambda *args, **kwargs: SimpleNamespace(
        model_dump=lambda: {
            "status": "partial", "started_at": "2026-10-22T10:00:00+00:00",
            "finished_at": "2026-10-22T10:00:01+00:00",
            "sources": [{
                "source_id": "cbr_press", "source": "Банк России — пресс-релизы", "status": "complete",
                "imported": 2, "duplicates": 1, "rejected": 0, "error": "",
            }, {
                "source_id": "cbr_events", "source": "Банк России — новости", "status": "error",
                "imported": 0, "duplicates": 0, "rejected": 0, "error": "timeout",
            }],
        }
    ))
    monkeypatch.setattr("app.main.prune_stored_news", lambda *args, **kwargs: {
        "removed_by_age": 0, "removed_by_count": 0, "remaining": 2,
    })
    monkeypatch.setattr("app.main.migrate_legacy_imports", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.main.stored_news_count", lambda: 2)
    with TestClient(app) as client:
        response = client.post("/api/settings/news/collect")
        latest = client.get("/api/settings/news/status")
    assert response.status_code == 200
    assert response.json()["sources"][1]["error"] == "timeout"
    assert latest.json()["latest"]["status"] == "partial"


def test_collected_news_is_stored_in_sqlite_and_retention_deletes_old_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.sqlite3"))
    init_app_db()
    from datetime import date, timedelta

    today = date.today()
    items = [
        {
            "id": "cbr-press-current", "source": "Банк России — пресс-релизы",
            "url": "https://www.cbr.ru/press/current", "title": "Новый порядок расчёта ставки",
            "published_at": today.isoformat(),
            "text": "Банк России опубликовал новый порядок расчёта ключевой ставки для финансового рынка.",
            "loaded_at": today.isoformat(), "sha256": "a" * 64,
        },
        {
            "id": "cbr-press-old", "source": "Банк России — пресс-релизы",
            "url": "https://www.cbr.ru/press/old", "title": "Старое сообщение о ставке",
            "published_at": (today - timedelta(days=100)).isoformat(),
            "text": "Старое сообщение Банка России о ключевой ставке и ситуации финансового рынка.",
            "loaded_at": today.isoformat(), "sha256": "b" * 64,
        },
    ]
    assert store_imported_news("cbr_press", items) == 2
    assert len(imported_news()) == 2
    result = prune_stored_news(retention_days=30, max_items=10)
    assert result == {"removed_by_age": 1, "removed_by_count": 0, "remaining": 1}
    assert [item["id"] for item in imported_news()] == ["cbr-press-current"]
