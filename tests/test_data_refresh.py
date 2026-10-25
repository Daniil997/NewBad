import pytest
from fastapi.testclient import TestClient

from app.main import app


def csrf(client):
    return {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}


def test_refresh_requires_csrf_and_runs_both_collectors(monkeypatch):
    called = []

    def news():
        called.append("news")
        return {"status": "complete", "sources": [{"imported": 3}]}

    def market():
        called.append("market")
        return {"status": "complete", "stored": 7}

    monkeypatch.setattr("app.main._collect_news", news)
    monkeypatch.setattr("app.main._collect_market", market)
    with TestClient(app) as client:
        assert client.post("/api/data/refresh").status_code == 403
        assert called == []
        response = client.post("/api/data/refresh", headers=csrf(client))
        assert response.status_code == 202
        state = client.get("/api/data/refresh").json()
    assert sorted(called) == ["market", "news"]
    assert state["status"] == "complete"
    assert state["news"]["sources"][0]["imported"] == 3
    assert state["market"]["stored"] == 7
    assert state["finished_at"] >= state["started_at"]


@pytest.mark.parametrize("news_status,market_status,expected", [
    ("partial", "complete", "partial"), ("error", "complete", "partial"),
    ("error", "error", "error"),
])
def test_partial_failures_are_visible(monkeypatch, news_status, market_status, expected):
    monkeypatch.setattr("app.main._collect_news", lambda: {"status": news_status})
    monkeypatch.setattr("app.main._collect_market", lambda: {"status": market_status})
    with TestClient(app) as client:
        client.post("/api/data/refresh", headers=csrf(client))
        assert client.get("/api/data/refresh").json()["status"] == expected


def test_collector_exception_does_not_prevent_other_collection_or_retry(monkeypatch):
    def fail():
        raise RuntimeError("unexpected upstream failure")

    monkeypatch.setattr("app.main._collect_news", fail)
    monkeypatch.setattr("app.main._collect_market", lambda: {"status": "complete", "stored": 2})
    with TestClient(app) as client:
        client.post("/api/data/refresh", headers=csrf(client))
        state = client.get("/api/data/refresh").json()
        assert state["status"] == "partial"
        assert state["market"]["stored"] == 2
        monkeypatch.setattr("app.main._collect_news", lambda: {"status": "complete"})
        assert client.post("/api/data/refresh", headers=csrf(client)).status_code == 202
        assert client.get("/api/data/refresh").json()["status"] == "complete"


def test_concurrent_refresh_is_rejected():
    from app.main import _DATA_REFRESH_LOCK

    with TestClient(app) as client, _DATA_REFRESH_LOCK:
        assert client.post("/api/data/refresh", headers=csrf(client)).status_code == 409


def test_removed_routes_are_unavailable_and_empty_database_has_no_fixture_news():
    from app.corpus.search import load_news

    paths = {route.path for route in app.routes}
    assert not any(path.startswith(("/api/clients", "/api/demo")) for path in paths)
    assert "/api/settings/demo/reset" not in paths
    assert "/api/settings/news/collect" in paths
    assert "/api/settings/market/collect" in paths
    assert load_news() == []
    with TestClient(app) as client:
        response = client.post("/api/settings", json={"task_routes": {"demo": {"chat": "mock"}}})
        assert response.status_code == 422
        assert set(client.get("/api/settings").json()["task_routes"]) == {"real"}


@pytest.mark.parametrize("kind,collector", [("news", "_collect_news"), ("market", "_collect_market")])
def test_developer_collection_uses_existing_collector_and_csrf(monkeypatch, kind, collector):
    called = []

    def collect():
        called.append(kind)
        return {"status": "complete", "source": kind}

    monkeypatch.setattr(f"app.main.{collector}", collect)
    with TestClient(app) as client:
        assert client.post(f"/api/settings/{kind}/collect").status_code == 403
        assert called == []
        result = client.post(f"/api/settings/{kind}/collect", headers=csrf(client))
    assert result.status_code == 200
    assert result.json()["source"] == kind
    assert called == [kind]
