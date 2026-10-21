from app.agent.loop import _gather, _plan
from app.agent.loop_context import (
    MAX_CONTEXT_CHARS,
    MAX_COUNTERPARTIES,
    MAX_DOCUMENTS,
    MAX_EXCERPT_CHARS,
    MAX_ISSUES,
    MAX_PAGE_CHARS,
    MAX_REASONS,
    AgentContext,
)
from app.agent.tools import find_news
from app.corpus.search import BankDocument
from app.llm.router import max_retries
from app.profile.build import get_profile


def test_profile_keeps_structure_and_drops_extra_counterparties():
    profile = get_profile("U-BORROWER")
    assert profile is not None
    crowded = profile.model_copy(update={"counterparties": list(profile.counterparties) * 20})
    ctx = AgentContext(client_id="U-BORROWER", event_id="cbr-rate-14")
    ctx.remember_profile(crowded)
    assert ctx.profile is not None
    assert ctx.profile.client_id == "U-BORROWER"
    assert ctx.profile.title
    assert ctx.profile.money_role
    assert ctx.profile.features
    assert ctx.profile.exposures
    assert ctx.profile.missing_data is not None
    assert len(ctx.profile.counterparties) == MAX_COUNTERPARTIES
    ctx.remember_counterparties(crowded.counterparties)
    assert len(ctx.counterparties) == MAX_COUNTERPARTIES


def test_gather_stores_news_documents_and_fragments(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctx = AgentContext(client_id="U-BORROWER", event_id="cbr-rate-14")
    _plan(ctx)
    _gather(ctx)
    assert ctx.profile is not None
    assert ctx.news is not None
    assert ctx.news.id == "cbr-rate-14"
    assert ctx.news_items[0].id == "cbr-rate-14"
    assert ctx.bank is not None
    assert ctx.bank.doc_id == "key_rate"
    assert len(ctx.documents) <= MAX_DOCUMENTS
    assert any(item.doc_id == "key_rate" for item in ctx.documents)
    fragments = [item for item in ctx.fragments if item.kind == "bank" and item.source_id == "key_rate"]
    assert fragments
    assert fragments[0].page == ctx.bank.page
    assert fragments[0].text in ctx.bank.text
    assert ctx.context_chars() <= MAX_CONTEXT_CHARS
    assert ctx.tool_reports
    assert all("payload" not in report for report in ctx.tool_reports)


def test_tool_error_is_kept_apart_from_empty_search_and_reasons_are_capped():
    ctx = AgentContext(client_id="U-BORROWER", event_id="cbr-rate-14")
    ctx.record_tool("search_bank_analytics", "none", "Нет релевантных материалов банка по этому запросу.")
    ctx.record_tool("search_bank_analytics", "error", "Индекс банка не построен: IOError")
    assert len(ctx.tool_errors) == 1
    assert ctx.tool_errors[0].tool == "search_bank_analytics"
    assert "Индекс банка не построен" in ctx.tool_errors[0].message
    assert ctx.tool_reports[0]["status"] == "none"
    assert "payload" not in ctx.tool_reports[0]
    for index in range(MAX_REASONS + 3):
        ctx.record_search_reason(f"повторный поиск {index}")
    assert len(ctx.search_reasons) == MAX_REASONS
    assert ctx.search_reasons[0] == "повторный поиск 0"


def test_validator_notes_and_attempt_stop_at_the_limit():
    ctx = AgentContext(client_id="U-BORROWER", event_id="cbr-rate-14")
    ctx.record_issues([f"number:{index}" for index in range(MAX_ISSUES + 5)])
    assert len(ctx.issues) == MAX_ISSUES
    assert ctx.issues[0] == "number:0"
    taken = 0
    while ctx.mark_attempt():
        taken += 1
    assert taken == max_retries()
    assert ctx.attempt == max_retries()
    assert ctx.can_retry() is False


def test_long_evidence_is_clipped_and_documents_do_not_grow_without_limit():
    news = find_news("cbr-rate-14")
    assert news is not None
    ctx = AgentContext(client_id="U-BORROWER", event_id="cbr-rate-14")
    ctx.remember_news(news.model_copy(update={"excerpt": "ф" * 5000}), primary=True)
    assert ctx.news is not None
    assert len(ctx.news.excerpt) == MAX_EXCERPT_CHARS
    quote = "цитата короткая и есть в тексте"
    padding = "абзац " * 800
    for index in range(MAX_DOCUMENTS + 2):
        document = BankDocument(
            doc_id=f"doc-{index}",
            title=f"Документ {index}",
            published_at="2026-09-11",
            topic="key_rate",
            page=1,
            source_type="bank_analytics",
            theses=["Короткий тезис."],
            text=f"{padding}{quote}{padding}",
            quote=quote,
        )
        ctx.remember_bank(document, None, primary=index == 0)
    assert len(ctx.documents) == MAX_DOCUMENTS
    assert ctx.bank is not None
    assert len(ctx.bank.text) <= MAX_PAGE_CHARS
    assert quote in ctx.bank.text
    assert ctx.context_chars() <= MAX_CONTEXT_CHARS
