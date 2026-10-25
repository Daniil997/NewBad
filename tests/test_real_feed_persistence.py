from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.corpus.search import NewsItem
from app.main import _cached_company_insight, app
from app.market_store import persist_profile_market_links
from app.personal_feed import (
    analysis_results,
    claim_job,
    enqueue_job,
    missing_candidates,
    save_job_results,
)
from app.user_store import connect_app, init_app_db, prune_insight_snapshots


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}


def _register(client: TestClient, login: str) -> None:
    response = client.post(
        "/api/auth/register",
        headers=_csrf(client),
        json={"login": login, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201


def _news(excerpt: str = "Регулятор сохранил ключевую ставку.") -> NewsItem:
    return NewsItem(
        id="news-001", url="https://example.org/news-001", title="Решение по ставке",
        published_at="2026-10-23", excerpt=excerpt, event_type="monetary_policy",
        event_title="Денежно-кредитная политика", source="Официальный источник", status="imported",
    )


def _profile_identity(login: str) -> tuple[str, int]:
    with connect_app() as connection:
        row = connection.execute(
            "SELECT p.id, p.profile_version FROM business_profiles p "
            "JOIN users u ON u.id = p.owner_id WHERE u.login = ?",
            (login,),
        ).fetchone()
    return row["id"], int(row["profile_version"])


def test_real_news_results_are_idempotent_and_scoped_to_profile_version(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "real.sqlite3"))
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    with TestClient(app) as owner, TestClient(app) as other:
        _register(owner, "feed_owner")
        _register(other, "feed_other")
        assert owner.get("/api/real/profile").json()["profile"]["status"] == "incomplete"
        assert other.get("/api/real/profile").json()["profile"]["status"] == "incomplete"
        profile_id, version = _profile_identity("feed_owner")
        other_profile_id, other_version = _profile_identity("feed_other")
        item = _news()

        assert missing_candidates(profile_id, version, [item]) == [item.id]
        job_id = enqueue_job(profile_id, version, [item.id])
        assert enqueue_job(profile_id, version, [item.id]) == job_id
        job = claim_job()
        assert job and job["id"] == job_id
        save_job_results(job, {"focus_topics": ["rates_inflation"]}, {item.id: item})

        assert missing_candidates(profile_id, version, [item]) == []
        assert missing_candidates(profile_id, version, [_news("Банк России оставил ставку без изменений.")]) == [item.id]
        assert analysis_results(other_profile_id, other_version)["items"] == {}
        assert missing_candidates(profile_id, version + 1, [item]) == [item.id]


def test_profile_market_links_are_persisted_idempotently_without_guessing_equities(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "market-links.sqlite3"))
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    catalog = {
        "currencies": {"instruments": ["USD", "CNY"]},
        "metals": {"instruments": ["GOLD"]},
        "equities": {"instruments": ["SBER"]},
    }
    profile = {"currency_interest": "interested", "focus_topics": ["commodity_prices"]}
    with TestClient(app) as client:
        _register(client, "market_owner")
        saved = client.put("/api/real/profile", headers=_csrf(client), json={
            "company_name": "ООО Пример", "description_skipped": True,
            "currency_interest": "interested", "focus_topics": ["commodity_prices"],
        })
        assert saved.status_code == 200
        profile_id, version = _profile_identity("market_owner")

    first = persist_profile_market_links(profile_id, version, profile, catalog)
    second = persist_profile_market_links(profile_id, version, profile, catalog)
    assert {(row["category"], row["instrument"]) for row in first} == {
        ("currencies", "USD"), ("currencies", "CNY"), ("metals", "GOLD"),
    }
    assert second == first
    assert all(row["reason"] for row in first)

    init_app_db()
    with connect_app() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        assert connection.execute("SELECT COUNT(*) FROM profile_market_links").fetchone()[0] == 3


def test_news_report_and_manual_insight_refresh_are_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "report-cache.sqlite3"))
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    item = _news()
    monkeypatch.setattr("app.main._fresh_real_news", lambda _days: [item])
    monkeypatch.setattr("app.main.market_snapshot", lambda: {"items": []})
    generated = []

    def fake_report(_profile, _item):
        generated.append(_item.id)
        return {"summary": "Проверяемый тестовый разбор", "evidence_ids": [f"news:{_item.id}"]}

    monkeypatch.setattr("app.main.analyze_profile_news", fake_report)
    with TestClient(app) as client:
        _register(client, "report_owner")
        csrf = _csrf(client)
        saved = client.put("/api/real/profile", headers=csrf, json={
            "company_name": "ООО Пример", "description_skipped": True,
        })
        assert saved.status_code == 200
        first = client.post(f"/api/real/news/{item.id}/report", headers=csrf, json={})
        cached = client.post(f"/api/real/news/{item.id}/report", headers=csrf, json={})
        refreshed = client.post(f"/api/real/news/{item.id}/report", headers=csrf, json={"refresh": True})
        before_refresh = client.get("/api/real/feed").json()
        refreshed_insight = client.post("/api/real/insight/refresh", headers=csrf, json={})
        after_refresh = client.get("/api/real/feed").json()

    assert first.status_code == 200 and first.json()["cached"] is False
    assert cached.status_code == 200 and cached.json()["cached"] is True
    assert refreshed.status_code == 200 and refreshed.json()["cached"] is False
    assert generated == [item.id, item.id]
    assert before_refresh["insight_updated_at"] is None
    assert refreshed_insight.status_code == 200
    assert refreshed_insight.json()["insight_updated_at"]
    assert after_refresh["insight_updated_at"] == refreshed_insight.json()["insight_updated_at"]


def test_expired_running_job_is_reclaimed_after_worker_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "queue-restart.sqlite3"))
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    with TestClient(app) as client:
        _register(client, "queue_owner")
        profile_id, version = _profile_identity("queue_owner")

    job_id = enqueue_job(profile_id, version, ["news-001"])
    first = claim_job()
    assert first["id"] == job_id and first["attempts"] == 0
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with connect_app() as connection:
        connection.execute(
            "UPDATE profile_analysis_jobs SET lease_until = ? WHERE id = ?", (expired, job_id),
        )
    restarted = claim_job()
    assert restarted["id"] == job_id
    assert restarted["attempts"] == 1


def test_interval_insight_refresh_and_snapshot_retention(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "insight-retention.sqlite3"))
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    monkeypatch.setattr("app.main.market_snapshot", lambda: {"items": []})
    monkeypatch.setattr("app.main.developer_settings", lambda: {
        "insight_retention_days": 90,
        "insight_refresh_mode": "interval",
        "insight_refresh_interval_hours": 1,
    })
    with TestClient(app) as client:
        _register(client, "insight_owner")
        saved_response = client.put("/api/real/profile", headers=_csrf(client), json={
            "company_name": "ООО Пример", "description_skipped": True,
        })
        assert saved_response.status_code == 200
        user_id = client.get("/api/auth/me").json()["user"]["id"]

    from app.user_store import business_profile_for_owner

    saved = business_profile_for_owner(user_id)
    first = _cached_company_insight(saved, [])
    second = _cached_company_insight(saved, [])
    assert first["updated_at"] == second["updated_at"]
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with connect_app() as connection:
        connection.execute(
            "UPDATE profile_insight_snapshots SET created_at = ? WHERE profile_id = ?",
            (old_time, saved["profile_id"]),
        )
    third = _cached_company_insight(saved, [])
    assert third["updated_at"] != second["updated_at"]

    stale_time = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    with connect_app() as connection:
        connection.execute(
            "INSERT INTO profile_insight_snapshots (profile_id, profile_version, input_hash, payload_json, created_at) "
            "VALUES (?, ?, 'expired', '{}', ?)",
            (saved["profile_id"], saved["profile_version"], stale_time),
        )
    assert prune_insight_snapshots(90) == 1
