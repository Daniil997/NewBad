from app.agent.draft import assemble, draft_card
from app.agent.loop_context import AgentContext
from app.agent.tools import (
    bank_query,
    find_bank,
    find_news,
    has_bank_topic,
    matched_exposures,
    run_tool,
)
from app.agent.verify import verify_card
from app.llm.router import choose_tools, max_retries
from app.profile.build import get_profile
from app.schemas.card import Card
from app.tracing.journal import log_step


def build_card(client_id: str, event_id: str) -> Card | None:
    if get_profile(client_id) is None:
        return None
    if find_news(event_id) is None:
        return None
    ctx = AgentContext(client_id=client_id, event_id=event_id)
    _plan(ctx)
    _gather(ctx)
    card = draft_card(ctx)
    log_step(client_id, event_id, "DRAFT", {"card_id": card.card_id})
    issues = verify_card(card, ctx)
    log_step(client_id, event_id, "VERIFY", {"issues": issues})
    while issues and ctx.retries < max_retries():
        ctx.retries += 1
        log_step(client_id, event_id, "REVISE", {"retry": ctx.retries, "issues": issues})
        card = draft_card(ctx)
        card.llm_trace.retries = ctx.retries
        issues = verify_card(card, ctx)
        log_step(client_id, event_id, "VERIFY", {"issues": issues, "retry": ctx.retries})
    if issues:
        card = assemble(ctx)
        card.llm_trace.retries = ctx.retries
        card.missing_data = [
            *card.missing_data,
            "После двух проверок черновик с неподтверждёнными числами или цитатой отклонён.",
        ]
        issues = verify_card(card, ctx)
    if issues:
        raise RuntimeError(f"Карточка не прошла проверку: {issues}")
    log_step(client_id, event_id, "FINALIZE", {"retries": ctx.retries})
    return card


def _plan(ctx: AgentContext) -> None:
    profile = get_profile(ctx.client_id)
    news = find_news(ctx.event_id)
    assert profile is not None and news is not None
    tools = ["get_profile", "search_news"]
    if has_bank_topic(news.event_type):
        tools.append("search_bank_analytics")
    if any(item.id == "counterparty" for item in profile.exposures):
        tools.append("get_counterparties")
    ctx.matched = matched_exposures(profile, news.event_type)
    choice = choose_tools(tools)
    ctx.plan = choice
    ctx.planned_tools = choice.tools
    log_step(ctx.client_id, ctx.event_id, "PLAN", {"tools": choice.tools, "matched": ctx.matched, "mode": choice.mode})


def _gather(ctx: AgentContext) -> None:
    for name in ctx.planned_tools:
        if name == "get_profile":
            payload = run_tool(name, {"client_id": ctx.client_id})
            ctx.profile = None if payload is None else get_profile(ctx.client_id)
        elif name == "search_news":
            run_tool(name, {"query": ctx.event_id})
            ctx.news = find_news(ctx.event_id)
        elif name == "search_bank_analytics":
            assert ctx.news is not None
            hits = run_tool(name, {"query": bank_query(ctx.news.event_type, ctx.news.title)})
            if isinstance(hits, list) and hits:
                ctx.bank = find_bank(str(hits[0]["id"]))
        elif name == "get_counterparties":
            run_tool(name, {"client_id": ctx.client_id})
            fresh = get_profile(ctx.client_id)
            ctx.counterparties = [] if fresh is None else list(fresh.counterparties)
    log_step(
        ctx.client_id,
        ctx.event_id,
        "GATHER",
        {"news": None if ctx.news is None else ctx.news.id, "bank": None if ctx.bank is None else ctx.bank.doc_id},
    )
