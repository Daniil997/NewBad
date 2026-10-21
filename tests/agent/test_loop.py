import json

from app.agent.draft import draft_card
from app.agent.loop import _gather, _plan, build_card
from app.agent.loop_context import AgentContext
from app.agent.verify import verify_card
from app.corpus.search import CorpusError
from app.db import connect


def _trace(client_id: str, event_id: str) -> list[tuple[str, dict]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT step, detail FROM agent_steps
            WHERE client_id = ? AND event_id = ?
            ORDER BY id
            """,
            (client_id, event_id),
        ).fetchall()
    return [(row["step"], json.loads(row["detail"])) for row in rows]


def _reports(trace: list[tuple[str, dict]], step: str) -> list[dict]:
    found = []
    for name, detail in trace:
        if name == step:
            found.extend(detail.get("reports") or [])
    return found


def test_successful_cycle_records_why_it_finished(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    card = build_card("U-BORROWER", "cbr-rate-14")
    assert card is not None
    trace = _trace("U-BORROWER", "cbr-rate-14")
    assert [step for step, _detail in trace] == ["PLAN", "GATHER", "DRAFT", "VERIFY", "FINALIZE"]
    assert trace[-1][1]["reason"] == "проверка пройдена"
    bank = [item for item in _reports(trace, "GATHER") if item["tool"] == "search_bank_analytics"]
    assert bank
    assert bank[0]["status"] == "ok"


def test_empty_search_is_not_a_tool_error(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.agent.tools.search_bank_analytics", lambda *_args, **_kwargs: [])
    card = build_card("U-BORROWER", "cbr-rate-14")
    assert card is not None
    assert card.bank_view.status == "none"
    reports = _reports(_trace("U-BORROWER", "cbr-rate-14"), "GATHER")
    bank = [item for item in reports if item["tool"] == "search_bank_analytics"]
    assert bank[0]["status"] == "none"
    assert all(item["status"] != "error" for item in reports)


def test_tool_error_stays_separate_from_an_empty_search(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def broken(*_args, **_kwargs):
        raise CorpusError("index", "Индекс банка не построен: IOError")

    monkeypatch.setattr("app.agent.tools.search_bank_analytics", broken)
    card = build_card("U-BORROWER", "cbr-rate-14")
    assert card is not None
    assert card.bank_view.status == "none"
    bank = [item for item in _reports(_trace("U-BORROWER", "cbr-rate-14"), "GATHER") if item["tool"] == "search_bank_analytics"]
    assert bank[0]["status"] == "error"
    assert "Индекс банка не построен" in bank[0]["message"]
    assert any("Индекс банка не построен" in item for item in card.missing_data)


def test_missing_evidence_repeats_a_targeted_search(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    calls = {"n": 0}
    real = draft_card

    def once(ctx):
        card = real(ctx)
        calls["n"] += 1
        if calls["n"] == 1:
            card.bank_view.quote = "этой фразы нет в паспорте"
        return card

    monkeypatch.setattr("app.agent.loop.draft_card", once)
    card = build_card("U-BORROWER", "cbr-rate-14")
    assert card is not None
    assert card.llm_trace.retries == 1
    assert card.bank_view.quote != "этой фразы нет в паспорте"
    trace = _trace("U-BORROWER", "cbr-rate-14")
    revise = [detail for step, detail in trace if step == "REVISE"]
    assert revise
    assert "Цитата не найдена" in revise[0]["reason"]
    repeated = [detail for step, detail in trace if step == "GATHER" and "reason" in detail]
    assert repeated
    assert repeated[0]["reports"][0]["tool"] == "search_bank_analytics"
    assert repeated[0]["reports"][0]["status"] in {"ok", "none"}


def test_same_search_is_not_called_twice(monkeypatch, tmp_path):
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
    trace = _trace("U-BORROWER", "cbr-rate-14")
    reasons = [detail["reason"] for step, detail in trace if step == "REVISE"]
    assert any("неподтверждённое число 99" in reason for reason in reasons)
    assert any("теми же аргументами" in reason for reason in reasons)
    searches = [detail for step, detail in trace if step == "GATHER" and "reason" in detail]
    assert len(searches) == 1
    assert searches[0]["reports"][0]["status"] == "none"
    assert trace[-1][1]["reason"].startswith("достигнут лимит повторов")


def test_time_budget_stops_the_cycle(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.agent.loop.time_budget_s", lambda: 0)
    real = draft_card

    def bad(ctx):
        card = real(ctx)
        fact = card.what_happened.claims[0]
        card.what_happened.claims[0] = fact.model_copy(update={"text": fact.text + " выдумано 99%"})
        return card

    monkeypatch.setattr("app.agent.loop.draft_card", bad)
    card = build_card("U-BORROWER", "cbr-rate-14")
    assert card is not None
    assert card.llm_trace.retries == 0
    assert "99%" not in card.model_dump_json()
    reason = _trace("U-BORROWER", "cbr-rate-14")[-1][1]["reason"]
    assert reason.startswith("истекло время цикла")
    assert "неподтверждённые утверждения" in reason


def test_final_card_has_no_unconfirmed_number(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    card = build_card("U-SAVER", "cbr-rate-14")
    assert card is not None
    ctx = AgentContext(client_id="U-SAVER", event_id="cbr-rate-14")
    _plan(ctx)
    _gather(ctx)
    assert verify_card(card, ctx) == []
