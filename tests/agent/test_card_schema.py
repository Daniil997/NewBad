import pytest
from pydantic import ValidationError

from app.agent.loop import build_card
from app.schemas.card import Card


def test_card_schema_separates_layers_and_records_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    card = build_card("U-BORROWER", "cbr-rate-14")
    assert card is not None
    assert {claim.layer for claim in card.what_happened.claims} == {"confirmed_fact"}
    assert {claim.layer for claim in card.bank_position} == {"bank_position"}
    assert {claim.layer for claim in card.why_for_company.claims} == {"model_inference"}
    news = next(item for item in card.sources if item.kind == "news")
    bank = next(item for item in card.sources if item.kind == "bank_analytics")
    assert news.title
    assert news.url
    assert news.published_at == "2026-09-11"
    assert news.quote
    assert bank.document == "key_rate"
    assert bank.page == 1
    assert bank.published_at == "2026-09-11"
    assert bank.quote == card.bank_view.quote
    assert card.profile_facts
    assert card.profile_facts[0].label
    assert card.profile_facts[0].value
    assert card.missing_data
    assert 0 <= card.confidence <= 1
    assert card.confidence == min(claim.confidence for claim in card.all_claims())
    assert card.verification.status == "passed"
    assert card.verification.issues == []
    assert card.follow_up == card.action.questions


def test_schema_rejects_an_unknown_field_and_a_mixed_layer(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    card = build_card("U-SAVER", "cbr-rate-14")
    assert card is not None
    payload = card.model_dump()
    payload["unlisted"] = "нет такого поля"
    with pytest.raises(ValidationError):
        Card.model_validate(payload)
    mixed = card.model_dump()
    mixed["what_happened"]["claims"][0]["layer"] = "bank_position"
    with pytest.raises(ValidationError):
        Card.model_validate(mixed)
    marked = card.model_dump()
    marked["verification"] = {"status": "passed", "issues": ["quote"]}
    with pytest.raises(ValidationError):
        Card.model_validate(marked)
