from fastapi.testclient import TestClient

from app.llm.router import budget_policy
from app.main import app
from app.schemas.card import Card
from eval.metrics import (
    bank_processing_coverage,
    factual_link_coverage,
    feed_quality,
    request_usage,
    source_supported_share,
    token_cost,
    unsupported_claim_ids,
)


def _cards(monkeypatch, tmp_path) -> list[Card]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    found = []
    with TestClient(app) as client:
        for client_id in ("U-BORROWER", "U-SAVER"):
            response = client.post(f"/api/clients/{client_id}/events/cbr-rate-14/card")
            assert response.status_code == 200
            found.append(Card.model_validate(response.json()))
    return found


def test_claims_are_linked_and_supported(monkeypatch, tmp_path):
    for card in _cards(monkeypatch, tmp_path):
        assert factual_link_coverage(card) == 1.0
        assert source_supported_share(card) == 1.0
        assert unsupported_claim_ids(card) == []


def test_top_quality_and_duplicates():
    borrower = feed_quality("U-BORROWER")
    saver = feed_quality("U-SAVER")
    peer = feed_quality("U-PEER")
    assert borrower["top_news_id"] == "cbr-rate-14"
    assert borrower["precision_at_3"] >= 1 / 3
    assert borrower["ndcg_at_5"] >= 0.5
    for report in (borrower, saver, peer):
        assert report["duplicate_ids"] == 0
        assert report["missing_labels"] == []
        assert report["grade_inversions"] == 0
        assert report["duplicate_below_fresh"] is True
        assert 0 <= report["precision_at_3"] <= 1
        assert 0 <= report["ndcg_at_5"] <= 1
    assert borrower["duplicate_penalty"] >= 1


def test_bank_documents_coverage_matches_the_index():
    report = bank_processing_coverage()
    assert report["catalog"] == 9
    assert report["passports"] >= 1
    assert report["pages_indexed"] + report["pages_skipped"] == report["pages_total"]
    assert report["pages_failed"] <= report["pages_skipped"]
    assert report["pages_total"] > 0
    assert 0 < report["document_coverage"] <= 1
    assert 0 < report["page_coverage"] <= 1
    key_rate = next(item for item in report["documents"] if item["id"] == "key_rate")
    assert key_rate["processed"] is True
    assert key_rate["indexed"] >= 1
    assert key_rate["failed"] == 0
    assert all(item["processed"] or item["reason"] for item in report["documents"])


def test_request_latency_tokens_and_cost_use_the_configured_rate(monkeypatch, tmp_path):
    cards = _cards(monkeypatch, tmp_path)
    assert budget_policy()["rub_per_1k_tokens"] == 0.0
    for card in cards:
        usage = request_usage(card)
        assert usage["latency_ms"] >= 0
        assert usage["tokens"] == card.llm_trace.tokens == 0
        assert usage["rub_per_1k_tokens"] == 0.0
        assert usage["cost_rub"] == token_cost(usage["tokens"], usage["rub_per_1k_tokens"])
    assert token_cost(0, 0.0) == 0.0
    assert token_cost(2000, 1.5) == 3.0
