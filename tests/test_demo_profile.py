from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.profile.build import get_original_profile
from app.user_store import connect_app

BASE = "/api/demo/clients/U-BORROWER/profile"


def csrf(client):
    return {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}


def register(client, login):
    response = client.post("/api/auth/register", headers=csrf(client), json={"login": login, "password": "correct-horse-battery"})
    assert response.status_code == 201


def test_demo_and_real_profiles_share_schema_validation_and_normalization(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "journal"))
    original = get_original_profile("U-BORROWER").model_dump()
    with TestClient(app) as client:
        register(client, "parity_owner")
        initial = client.get(BASE)
        assert initial.status_code == 200
        payload = {"company_name": "  Новая компания  ", "website": "example.org", "description": "Производство упаковки",
                   "city": "Казань", "industry_tags": ["manufacturing", "manufacturing"], "focus_topics": ["tax_regulation"],
                   "credit_interest": "not_relevant", "deposit_interest": "interested"}
        demo = client.put(BASE, headers=csrf(client), json=payload)
        real = client.put("/api/real/profile", headers=csrf(client), json=payload)
        assert demo.status_code == real.status_code == 200
        demo_data, real_data = demo.json()["profile"], real.json()["profile"]
        for key in demo_data.keys() - {"updated_at", "profile_version"}:
            assert demo_data[key] == real_data[key]
        assert client.get(BASE + "/options").json() == client.get("/api/real/profile/options").json()
        again = client.put(BASE, headers=csrf(client), json=payload)
        assert again.json()["profile"]["profile_version"] == demo_data["profile_version"]
        projected = client.get("/api/clients/U-BORROWER").json()
        assert projected["title"] == "Новая компания"
        assert next(item for item in projected["features"] if item["id"] == "CITY")["value"] == "Казань"
        assert "rate_borrower" not in {item["id"] for item in projected["exposures"]}
        assert client.get("/api/demo/clients/U-SAVER/profile").json()["profile"]["company_name"] != "Новая компания"
    assert get_original_profile("U-BORROWER").model_dump() == original
    with connect_app() as connection:
        assert connection.execute("SELECT COUNT(*) FROM business_profiles").fetchone()[0] == 1


def test_demo_requires_csrf_but_does_not_grant_real_account_access():
    with TestClient(app) as client:
        assert client.get(BASE).status_code == 200
        assert client.get("/api/real/profile").status_code == 401
        assert client.put(BASE, json={"company_name": "Без токена"}).status_code == 403
        assert client.get("/api/demo/clients/missing/profile").status_code == 404
        invalid = client.put(BASE, headers=csrf(client), json={"industry_tags": ["unknown-tag"]})
        assert invalid.status_code == 422
        assert client.get("/api/real/profile").status_code == 401


@pytest.mark.parametrize("base", [BASE, "/api/real/profile"])
def test_site_review_has_the_same_consent_provenance_and_manual_override_rules(base, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "journal"))
    monkeypatch.setattr("app.main.resolve_route", lambda *args, **kwargs: SimpleNamespace(provider="openrouter", model="test-model"))
    monkeypatch.setattr("app.main.crawl_site", lambda _: SimpleNamespace(pages=[SimpleNamespace(url="https://example.org/about", title="О компании")], warnings=[]))
    proposal = {"field_name": "products_services", "value": "Упаковка", "evidence": "Производим упаковку.",
                "source_url": "https://example.org/about", "source_title": "О компании", "confidence": .9}
    monkeypatch.setattr("app.main.extract_profile_suggestions", lambda *args: SimpleNamespace(suggestions=[proposal], model="test-model", provider="openrouter", warning=""))
    with TestClient(app) as client:
        if base.startswith("/api/real"):
            register(client, "site_parity_owner")
        else:
            client.get(base)
        payload = {"company_name": "Компания", "website": "example.org", "description_skipped": True}
        assert client.put(base, headers=csrf(client), json=payload).status_code == 200
        assert client.post(base + "/site-analysis", headers=csrf(client), json={"consent": False}).status_code == 400
        scanned = client.post(base + "/site-analysis", headers=csrf(client), json={"consent": True})
        assert scanned.status_code == 200
        assert client.get(base).json()["profile"]["products_services"] == ""
        # A different demo company cannot see or accept these suggestions.
        if base == BASE:
            other = "/api/demo/clients/U-SAVER/profile"
            assert client.get(other + "/suggestions").json()["suggestions"] == []
            rejected = client.post(other + "/suggestions/decision", headers=csrf(client), json={"field_name": proposal["field_name"], "source_url": proposal["source_url"], "accept": True})
            assert rejected.status_code == 404
        accepted = client.post(base + "/suggestions/apply", headers=csrf(client))
        assert accepted.status_code == 200
        assert accepted.json()["profile"]["products_services"] == "Упаковка"
        assert len(client.get(base + "/sources").json()["sources"]) == 1
        client.put(base, headers=csrf(client), json={**payload, "products_services": "Другое значение"})
        assert client.get(base + "/sources").json()["sources"] == []
        assert client.post(base + "/site-analysis", headers=csrf(client), json={"consent": True}).status_code == 429


def test_saving_demo_profile_invalidates_its_cached_feed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "journal"))
    with TestClient(app) as client:
        first = client.get("/api/clients/U-BORROWER/feed")
        assert first.status_code == 200
        client.get(BASE)
        edited = client.put(BASE, headers=csrf(client), json={"company_name": "Заёмщик", "description_skipped": True,
                                                           "credit_interest": "not_relevant", "deposit_interest": "interested"})
        assert edited.status_code == 200
        second = client.get("/api/clients/U-BORROWER/feed")
        first_rate = next(item for item in first.json()["top"] + first.json()["less_relevant"] if item["news_id"] == "cbr-rate-14")
        next_rate = next(item for item in second.json()["top"] + second.json()["less_relevant"] if item["news_id"] == "cbr-rate-14")
        assert first_rate["reason"] != next_rate["reason"]


def test_edited_topics_change_demo_ranking_without_claiming_bank_products(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "journal"))
    with TestClient(app) as client:
        client.get(BASE)
        client.put(BASE, headers=csrf(client), json={"company_name": "Компания", "description_skipped": True,
                                                   "focus_topics": ["exchange_rates"], "currency_interest": "interested"})
        response = client.get("/api/clients/U-BORROWER/feed")
        assert response.status_code == 200
        items = response.json()["top"] + response.json()["less_relevant"]
        fx = next(item for item in items if item["event_type"] == "fx")
        assert any(factor["id"] == "preferences" for factor in fx["factors"])
        profile = client.get("/api/clients/U-BORROWER").json()
        exposure = next(item for item in profile["exposures"] if item["id"] == "fx_exposure")
        assert exposure["basis"] == "hypothesis"
        assert exposure["source"] == "client_input"
