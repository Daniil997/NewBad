import json
import sqlite3

import pytest

from app.agent.loop import build_card
from app.db import JournalError, init_db
from app.profile.build import get_profile
from app.tracing.journal import log_step, log_tool_call, read_trace, trace_scope


def test_trace_keeps_steps_tools_and_route(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    card = build_card("U-BORROWER", "cbr-rate-14")
    assert card is not None
    trace = read_trace("U-BORROWER", "cbr-rate-14")
    assert [item["step"] for item in trace["steps"]][:4] == ["PLAN", "GATHER", "DRAFT", "VERIFY"]
    assert trace["steps"][-1]["step"] == "FINALIZE"
    assert trace["steps"][-1]["detail"]["reason"] == "проверка пройдена"
    tools = {item["tool"] for item in trace["tools"]}
    assert {"get_profile", "search_news", "search_bank_analytics"} <= tools
    bank = read_trace("U-BORROWER", "cbr-rate-14", query="ключевая ставка")
    assert bank["tools"]
    assert {item["tool"] for item in bank["tools"]} == {"search_bank_analytics"}
    assert bank["tools"][0]["result"]["status"] == "ok"
    assert bank["tools"][0]["result"]["items"][0]["evidence_id"] == "key_rate:1"
    call = trace["llm"][0]
    assert call["provider"] == "mock"
    assert call["model"] == "mock-model"
    assert call["mode"] == "mock"
    assert call["tokens"] == 0
    assert call["latency_ms"] == 0
    assert "mock" in call["switch_reason"]


def test_journal_drops_secrets_and_profile_values(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    profile = get_profile("U-BORROWER")
    assert profile is not None
    with trace_scope("U-BORROWER", "cbr-rate-14"):
        log_tool_call(
            "get_profile",
            {"client_id": "U-BORROWER", "api_key": "sk-secret-value"},
            {
                "status": "ok",
                "source": "company_profile",
                "api_key": "sk-secret-value",
                "items": [{"evidence_id": "U-BORROWER", "source": "company_profile", "value": "секрет выручки"}],
            },
        )
    blob = json.dumps(read_trace("U-BORROWER", "cbr-rate-14"), ensure_ascii=False)
    assert "sk-secret-value" not in blob
    assert "секрет выручки" not in blob
    assert "U-BORROWER" in blob
    for feature in profile.features:
        if len(feature.value) > 4:
            assert feature.value not in blob


def test_second_run_keeps_history_and_returns_the_latest(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    init_db()
    init_db()
    assert build_card("U-BORROWER", "cbr-rate-14") is not None
    assert build_card("U-BORROWER", "cbr-rate-14") is not None
    latest = read_trace("U-BORROWER", "cbr-rate-14")
    assert latest["steps"][0]["step"] == "PLAN"
    assert latest["steps"][-1]["detail"]["reason"] == "проверка пройдена"
    assert [item["step"] for item in latest["steps"]].count("PLAN") == 1


def test_old_journal_gains_trace_columns(monkeypatch, tmp_path):
    path = tmp_path / "journal.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE tool_calls (
            id INTEGER PRIMARY KEY,
            tool TEXT NOT NULL,
            arguments TEXT NOT NULL,
            created_at TEXT NOT NULL,
            result_hash TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE llm_calls (
            id INTEGER PRIMARY KEY,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            mode TEXT NOT NULL,
            tokens INTEGER,
            latency_ms INTEGER,
            prompt_hash TEXT,
            switch_reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    init_db()
    with trace_scope("U-BORROWER", "cbr-rate-14"):
        log_tool_call("search_news", {"query": "ставка"}, {"status": "none", "message": "пусто"})
    found = read_trace("U-BORROWER", "cbr-rate-14", query="ставка")
    assert found["tools"][0]["result"]["status"] == "none"


def test_database_error_is_explicit(monkeypatch, tmp_path):
    blocked = tmp_path / "journal.sqlite3"
    blocked.mkdir()
    monkeypatch.setattr("app.db.db_path", lambda: blocked)
    with pytest.raises(JournalError, match="Журнал недоступен"):
        log_step("U-BORROWER", "cbr-rate-14", "PLAN", {"tools": []})
