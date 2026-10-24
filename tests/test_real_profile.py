from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.main import app


def _csrf(client: TestClient) -> dict[str, str]:
    response = client.get("/api/auth/csrf")
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _register(client: TestClient, login: str) -> None:
    response = client.post(
        "/api/auth/register",
        headers=_csrf(client),
        json={"login": login, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201


def test_real_profile_requires_a_session_and_csrf(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    with TestClient(app) as client:
        anonymous_profile = client.get("/api/real/profile")
        anonymous_options = client.get("/api/real/profile/options")
        _register(client, "profile_owner")
        profile = client.get("/api/real/profile")
        blocked_update = client.put("/api/real/profile", json={"company_name": "Без CSRF"})

    assert anonymous_profile.status_code == 401
    assert anonymous_options.status_code == 401
    assert profile.status_code == 200
    assert profile.json()["profile"]["status"] == "incomplete"
    assert profile.json()["profile"]["company_name"] == ""
    assert profile.json()["profile"]["credit_interest"] == "unknown"
    assert blocked_update.status_code == 403


def test_profile_persists_partial_values_and_validates_taxonomy(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    with TestClient(app) as client:
        _register(client, "profile_writer")
        csrf = _csrf(client)
        options = client.get("/api/real/profile/options")
        payload = {
            "company_name": "  ООО Пример  ",
            "website": "example.org",
            "description": "Производим упаковку для пищевой промышленности.",
            "industry_tags": ["manufacturing", "manufacturing"],
            "focus_topics": ["commodity_prices"],
            "credit_interest": "interested",
            "region": "  ",
        }
        saved = client.put("/api/real/profile", headers=csrf, json=payload)
        same = client.put("/api/real/profile", headers=csrf, json=payload)
        invalid_tag = client.put(
            "/api/real/profile",
            headers=csrf,
            json={**payload, "industry_tags": ["not-in-the-taxonomy"]},
        )

    assert options.status_code == 200
    assert {item["id"] for item in options.json()["industry_tags"]} >= {"manufacturing", "trade"}
    assert saved.status_code == 200
    body = saved.json()["profile"]
    assert body["status"] == "complete"
    assert body["company_name"] == "ООО Пример"
    assert body["website"] == "https://example.org"
    assert body["region"] == ""
    assert body["industry_tags"] == ["manufacturing"]
    assert body["credit_interest"] == "interested"
    assert same.json()["profile"]["profile_version"] == body["profile_version"]
    assert invalid_tag.status_code == 422
    assert "body.industry_tags" in invalid_tag.json()["fields"]


def test_real_profiles_are_isolated_between_accounts(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    with TestClient(app) as first, TestClient(app) as second:
        _register(first, "company_one")
        _register(second, "company_two")
        saved = first.put(
            "/api/real/profile",
            headers=_csrf(first),
            json={"company_name": "Компания Первая", "description_skipped": True},
        )
        first_profile = first.get("/api/real/profile").json()["profile"]
        second_profile = second.get("/api/real/profile").json()["profile"]

    assert saved.status_code == 200
    assert first_profile["status"] == "complete"
    assert first_profile["company_name"] == "Компания Первая"
    assert second_profile["status"] == "incomplete"
    assert second_profile["company_name"] == ""


def test_site_analysis_requires_consent_and_writes_empty_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    monkeypatch.setattr("app.main.resolve_route", lambda tier, **_route_options: SimpleNamespace(provider="openrouter", model="allowed-model"))
    monkeypatch.setattr("app.main.crawl_site", lambda url: SimpleNamespace(
        pages=[SimpleNamespace(url="https://example.org/", title="О компании", text="Компания ООО Север производит упаковку.")],
        warnings=[],
    ))
    monkeypatch.setattr("app.main.extract_profile_suggestions", lambda pages, profile: SimpleNamespace(
        suggestions=[{
            "field_name": "company_name", "value": "ООО Север", "evidence": "Компания ООО Север производит упаковку.",
            "source_url": pages[0].url, "source_title": pages[0].title, "confidence": 0.9,
        }], model="allowed-model", provider="openrouter", warning="",
    ))
    with TestClient(app) as client:
        _register(client, "site_profile_owner")
        csrf = _csrf(client)
        client.put("/api/real/profile", headers=csrf, json={"website": "https://example.org"})
        no_consent = client.post("/api/real/profile/site-analysis", headers=csrf, json={"consent": False})
        analyzed = client.post("/api/real/profile/site-analysis", headers=csrf, json={"consent": True})
        after = client.get("/api/real/profile").json()["profile"]
        pending = client.get("/api/real/profile/suggestions").json()["suggestions"]
        too_soon = client.post("/api/real/profile/site-analysis", headers=csrf, json={"consent": True})
        # Only an explicit acceptance may fill the company's profile.
        accepted = client.post("/api/real/profile/suggestions/apply", headers=csrf)
        filled = client.get("/api/real/profile").json()["profile"]
        still_pending = client.get("/api/real/profile/suggestions").json()["suggestions"]

    assert no_consent.status_code == 400
    assert analyzed.status_code == 200
    assert analyzed.json()["status"] == "suggestions_ready"
    assert analyzed.json()["filled_fields"] == []
    assert analyzed.json()["profile"]["company_name"] == ""
    assert analyzed.json()["pages"][0]["url"] == "https://example.org/"
    assert after["company_name"] == ""
    assert len(pending) == 1
    assert too_soon.status_code == 429
    assert accepted.status_code == 200
    assert accepted.json()["filled_fields"] == ["company_name"]
    assert filled["company_name"] == "ООО Север"
    assert still_pending == []
