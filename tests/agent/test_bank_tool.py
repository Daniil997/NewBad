import json

import pytest

from app.agent.draft import assemble
from app.agent.loop_context import AgentContext
from app.agent.tools import bank_analytics, find_bank, find_news, run_tool
from app.corpus.search import CorpusError, load_bank_documents
from app.llm.router import PlanChoice
from app.profile.build import get_profile

_UP = "Рост цен на металлы ускорился и остаётся высоким для этой отрасли"
_DOWN = "Снижение цен на металлы замедлило выручку компаний этой отрасли"
_SAME = "Рост цен на металлы ускорился и поддерживает выручку компаний отрасли"


def _passport(folder, doc_id: str, published_at: str, quote: str, page: int) -> None:
    payload = {
        "doc_id": doc_id,
        "title": f"Обзор {doc_id}",
        "published_at": published_at,
        "topic": "commodities",
        "page": page,
        "source_type": "bank_analytics",
        "theses": [],
        "text": f"{quote}. Материал подготовлен аналитиками банка для компаний отрасли.",
        "quote": quote,
    }
    (folder / f"{doc_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_tool_returns_quote_with_document_and_page():
    result = bank_analytics("ключевая ставка")
    assert result["status"] == "found"
    assert result["message"] is None
    hit = result["hits"][0]
    assert hit["id"] == "key_rate"
    assert hit["page"] == 1
    assert hit["source_type"] == "bank_analytics"
    assert hit["published_at"] == "2026-09-11"
    document = find_bank(hit["id"], hit["page"])
    assert document is not None
    assert hit["quote"] == document.quote
    assert hit["quote"] in document.text
    assert len(hit["quote"].split()) <= 25


def test_missing_sources_are_explicit_and_load_errors_are_not(tmp_path):
    empty = bank_analytics("данные")
    assert empty["status"] == "none"
    assert empty["hits"] == []
    assert "Нет релевантных материалов банка" in empty["message"]

    assert bank_analytics("инфляция", folder=tmp_path)["status"] == "none"
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(CorpusError) as error:
        bank_analytics("инфляция", folder=tmp_path)
    assert error.value.kind == "load"


def test_conflict_reports_different_dates_and_conclusions(tmp_path):
    _passport(tmp_path, "jan", "2026-01-15", _UP, 2)
    _passport(tmp_path, "jun", "2026-06-01", _DOWN, 4)
    result = bank_analytics("цен на металлы", folder=tmp_path)
    assert result["status"] == "conflict"
    assert "2026-01-15" in result["message"]
    assert "2026-06-01" in result["message"]
    assert "стр. 2" in result["message"]
    assert "стр. 4" in result["message"]
    assert "расходятся" in result["message"]
    by_id = {hit["id"]: hit for hit in result["hits"]}
    assert by_id["jan"]["quote"] == _UP
    assert by_id["jun"]["page"] == 4

    same = tmp_path / "same"
    same.mkdir()
    _passport(same, "march", "2026-03", _SAME, 1)
    _passport(same, "march-day", "2026-03-20", _SAME, 3)
    agreed = bank_analytics("цен на металлы", folder=same)
    assert agreed["status"] == "found"
    assert agreed["message"] is None
    assert {hit["page"] for hit in agreed["hits"]} == {1, 3}


def test_run_tool_uses_the_bank_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = run_tool("search_bank_analytics", {"query": "ключевая ставка"})
    assert result["status"] == "found"
    assert result["hits"][0]["quote"]
    page = find_bank("inflation", 2)
    assert page is not None
    assert page.page == 2
    assert page.doc_id == "inflation"
    assert find_bank("inflation").page == 1


def test_card_keeps_absence_and_conflict_visible():
    profile = get_profile("U-BORROWER")
    news = find_news("cbr-rate-14")
    plan = PlanChoice(tools=["get_profile"], model="mock", provider="mock", mode="mock")
    ctx = AgentContext(
        client_id="U-BORROWER",
        event_id="cbr-rate-14",
        profile=profile,
        news=news,
        plan=plan,
        matched=["rate_borrower"],
        bank_note="Нет релевантных материалов банка по этому запросу.",
    )
    missing = assemble(ctx)
    assert missing.bank_view.status == "none"
    assert ctx.bank_note in missing.missing_data

    document = next(item for item in load_bank_documents() if item.doc_id == "key_rate")
    ctx.bank = document
    ctx.bank_note = "Даты источников различаются: обзор от 2026-01-15, стр. 2."
    conflict = assemble(ctx)
    assert conflict.bank_view.status == "found"
    assert conflict.bank_view.doc_ref == "key_rate"
    assert conflict.bank_view.page == document.page
    assert conflict.bank_view.quote == document.quote
    assert ctx.bank_note in conflict.assumptions
