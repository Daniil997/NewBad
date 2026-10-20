import json

from fastapi.testclient import TestClient

from app.agent.loop import build_card, draft_card
from app.db import ROOT, connect
from app.main import app


def _why(card: dict) -> str:
    return " ".join(item["text"] for item in card["why_for_company"]["claims"])


def _layers(card: dict) -> set[str]:
    claims = (
        card["what_happened"]["claims"]
        + card["why_for_company"]["claims"]
        + card["consequences"]["risks"]
        + card["consequences"]["opportunities"]
    )
    return {item["layer"] for item in claims}


def test_rate_cards_differ_for_borrower_and_saver(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as client:
        borrower = client.post("/api/clients/U-BORROWER/events/cbr-rate-14/card")
        saver = client.post("/api/clients/U-SAVER/events/cbr-rate-14/card")
        missing = client.post("/api/clients/U-BORROWER/events/missing-event/card")

    assert borrower.status_code == 200
    assert saver.status_code == 200
    assert missing.status_code == 404
    borrower_card = borrower.json()
    saver_card = saver.json()
    assert "стоимость заёмных денег" in _why(borrower_card)
    assert "доходность размещения" not in _why(borrower_card)
    assert "доходность размещения" in _why(saver_card)
    assert "стоимость заёмных денег" not in _why(saver_card)
    assert {"confirmed_fact", "bank_position", "model_inference"} <= _layers(borrower_card)
    assert {"confirmed_fact", "bank_position", "model_inference"} <= _layers(saver_card)

    passport = json.loads((ROOT / "data" / "bank" / "key_rate.json").read_text(encoding="utf-8"))
    for card in (borrower_card, saver_card):
        assert card["bank_view"]["quote"] == passport["quote"]
        assert card["bank_view"]["quote"] in passport["text"]
        assert card["llm_trace"]["mode"] == "mock"
        assert card["llm_trace"]["provider"] == "mock"

    with connect() as connection:
        tools = {row["tool"] for row in connection.execute("SELECT tool FROM tool_calls")}
        steps = [
            row["step"]
            for row in connection.execute(
                "SELECT step FROM agent_steps WHERE client_id = 'U-BORROWER' ORDER BY id"
            )
        ]
        llm = connection.execute("SELECT mode, provider FROM llm_calls").fetchone()
    assert {"get_profile", "search_news", "search_bank_analytics", "get_counterparties"} <= tools
    assert steps[:4] == ["PLAN", "GATHER", "DRAFT", "VERIFY"]
    assert steps[-1] == "FINALIZE"
    assert llm["mode"] == "mock"
    assert llm["provider"] == "mock"


def test_failed_draft_is_dropped_after_two_retries(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    real = draft_card

    def bad(ctx):
        card = real(ctx)
        fact = card.what_happened.claims[0]
        card.what_happened.claims[0] = fact.model_copy(update={"text": fact.text + " выдумано 99%"})
        return card

    monkeypatch.setattr("app.agent.loop.draft_card", bad)
    card = build_card("U-BORROWER", "cbr-rate-14")
    assert card is not None
    assert card.llm_trace.retries == 2
    assert "99%" not in card.model_dump_json()
    assert any("неподтвержд" in item for item in card.missing_data)


def test_verify_rejects_quote_outside_passport(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.agent.loop import _gather, _plan
    from app.agent.loop_context import AgentContext
    from app.agent.verify import verify_card

    ctx = AgentContext(client_id="U-BORROWER", event_id="cbr-rate-14")
    _plan(ctx)
    _gather(ctx)
    card = draft_card(ctx)
    card.bank_view.quote = "этой фразы нет в паспорте"
    assert "quote" in verify_card(card, ctx)
