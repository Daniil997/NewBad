"""Regression coverage for source attribution and changes during site scans."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.user_store import (
    accepted_profile_sources, business_profile_for_owner, claim_site_analysis,
    create_user, decide_profile_suggestion, finish_site_analysis, init_app_db,
    pending_profile_suggestions, save_business_profile, save_profile_suggestions,
)


@pytest.fixture
def owner(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.sqlite3"))
    init_app_db()
    user_id = create_user("site_owner", "unused-test-hash")["id"]
    save_business_profile(user_id, {"website": "https://mts.ru", "company_name": ""}, "incomplete")
    return user_id


def proposal(field="company_name", value="МТС"):
    return {"field_name": field, "value": value, "evidence": "МТС — цифровые сервисы",
            "source_url": "https://mts.ru/", "source_title": "МТС", "confidence": 0.9}


def test_changing_website_discards_pending_data_and_inflight_old_result(owner):
    save_profile_suggestions(owner, [proposal()])
    save_business_profile(owner, {"website": "https://www.kaspersky.ru"}, "incomplete")
    assert pending_profile_suggestions(owner) == []
    assert save_profile_suggestions(owner, [proposal()], expected_website="https://mts.ru") == []
    assert pending_profile_suggestions(owner) == []


def test_manual_edits_remove_only_pending_values_for_filled_fields(owner):
    save_profile_suggestions(owner, [proposal(), proposal("description", "Цифровые сервисы")])
    save_business_profile(owner, {"website": "https://mts.ru", "company_name": "Мой бренд"}, "incomplete")
    assert [row["field_name"] for row in pending_profile_suggestions(owner)] == ["description"]


@pytest.mark.parametrize("accept", [True, False])
def test_explicit_rescan_can_offer_a_previously_reviewed_empty_field(owner, accept):
    item = proposal()
    save_profile_suggestions(owner, [item])
    decide_profile_suggestion(owner, item["field_name"], item["source_url"], accept=accept)
    save_business_profile(owner, {"website": "https://mts.ru", "company_name": ""}, "incomplete")
    assert accepted_profile_sources(owner) == []
    assert len(save_profile_suggestions(owner, [item])) == 1
    assert decide_profile_suggestion(owner, item["field_name"], item["source_url"], accept=True) == "accepted"
    assert len(accepted_profile_sources(owner)) == 1


def test_configuration_failure_does_not_force_five_minute_wait(owner):
    run_id, _ = claim_site_analysis(owner)
    assert claim_site_analysis(owner) == "busy"
    finish_site_analysis(run_id, status="failed", message="model_unavailable")
    retry_id, _ = claim_site_analysis(owner)
    finish_site_analysis(retry_id, status="completed", pages_scanned=1)
    assert claim_site_analysis(owner) == "cooldown"


def test_concurrent_acceptance_preserves_both_fields_and_their_sources(owner):
    items = [proposal(), proposal("description", "Цифровые сервисы")]
    save_business_profile(owner, {"website": "https://mts.ru", "description_skipped": True}, "incomplete")
    save_profile_suggestions(owner, items)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: decide_profile_suggestion(
            owner, item["field_name"], item["source_url"], accept=True,
        ), items))
    assert results == ["accepted", "accepted"]
    saved = business_profile_for_owner(owner)
    assert saved["status"] == "complete"
    assert saved["profile"]["company_name"] == "МТС"
    assert saved["profile"]["description"] == "Цифровые сервисы"
    assert saved["profile"]["description_skipped"] is False
    assert len(accepted_profile_sources(owner)) == 2
