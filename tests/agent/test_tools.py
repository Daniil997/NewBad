import json

import pytest

from app.agent.tools import run_tool
from app.corpus.search import CorpusError
from app.db import connect


def _calls() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT tool, arguments, result_hash FROM tool_calls ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def test_four_tools_share_one_result_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    results = [
        run_tool("get_profile", {"client_id": "U-BORROWER"}),
        run_tool("search_news", {"query": "cbr-rate-14"}),
        run_tool("search_bank_analytics", {"query": "ключевая ставка"}),
        run_tool("get_counterparties", {"client_id": "U-BORROWER"}),
    ]
    assert [item["tool"] for item in results] == [
        "get_profile",
        "search_news",
        "search_bank_analytics",
        "get_counterparties",
    ]
    for result in results:
        assert set(result) == {"tool", "status", "evidence_id", "source", "message", "items"}
        assert result["status"] == "ok"
        assert result["evidence_id"]
        assert result["source"]
        for item in result["items"]:
            assert item["evidence_id"]
            assert item["source"]
    assert results[0]["source"] == "company_profile"
    assert results[0]["items"][0]["client_id"] == "U-BORROWER"
    assert results[1]["items"][0]["evidence_id"] == "cbr-rate-14"
    assert results[1]["items"][0]["source"] == "https://www.cbr.ru/press/"
    assert results[2]["items"][0]["evidence_id"] == "key_rate:1"
    assert results[3]["source"] == "payments"
    assert all(item["source"] == "payments" for item in results[3]["items"])


def test_empty_search_has_no_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    missed = run_tool("get_profile", {"client_id": "U-MISSING"})
    assert missed["status"] == "none"
    assert missed["items"] == []
    assert missed["evidence_id"] == ""
    assert missed["message"] == "Компания не найдена."
    quiet = run_tool("search_news", {"query": "qqqq-absent-token"})
    assert quiet["status"] == "none"
    assert quiet["items"] == []


def test_bad_arguments_are_rejected_and_traced(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="пустой параметр"):
        run_tool("search_news", {"query": "  "})
    with pytest.raises(ValueError, match="белого списка"):
        run_tool("search_web", {"query": "ключ"})
    with pytest.raises(ValueError, match="только параметр"):
        run_tool("get_profile", {"client_id": "U-BORROWER", "extra": "1"})
    rows = _calls()
    assert [row["tool"] for row in rows] == ["search_news", "search_web", "get_profile"]
    assert all(row["result_hash"] for row in rows)
    arguments = [json.loads(row["arguments"]) for row in rows]
    assert arguments[0] == {"query": "  "}
    assert arguments[2]["client_id"] == "U-BORROWER"


def test_successful_call_is_traced_with_its_arguments(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    run_tool("search_news", {"query": "cbr-rate-14"})
    row = _calls()[-1]
    assert row["tool"] == "search_news"
    assert json.loads(row["arguments"]) == {"query": "cbr-rate-14"}
    assert row["result_hash"]


def test_corpus_error_is_traced_and_not_turned_into_an_empty_result(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def broken(*_args, **_kwargs):
        raise CorpusError("index", "Индекс банка не построен: IOError")

    monkeypatch.setattr("app.agent.tools.search_bank_analytics", broken)
    with pytest.raises(CorpusError):
        run_tool("search_bank_analytics", {"query": "ключевая ставка"})
    row = _calls()[-1]
    assert row["tool"] == "search_bank_analytics"
    assert json.loads(row["arguments"]) == {"query": "ключевая ставка"}
