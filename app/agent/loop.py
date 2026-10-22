import json
import time

from app.agent.draft import assemble, draft_card
from app.agent.generate import extract_fact
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
from app.corpus.search import CorpusError
from app.llm.router import choose_tools, time_budget_s
from app.profile.build import get_profile
from app.schemas.card import Card
from app.tracing.journal import log_step, trace_scope


def build_card(client_id: str, event_id: str) -> Card | None:
    if get_profile(client_id) is None:
        return None
    if find_news(event_id) is None:
        return None
    ctx = AgentContext(client_id=client_id, event_id=event_id)
    started = time.perf_counter()
    with trace_scope(client_id, event_id):
        _plan(ctx)
        _gather(ctx)
        card = draft_card(ctx)
        log_step(client_id, event_id, "DRAFT", {"card_id": card.card_id})
        issues = verify_card(card, ctx)
        ctx.record_issues(issues)
        log_step(client_id, event_id, "VERIFY", {"issues": issues})
        while issues and not _expired(started) and ctx.mark_attempt():
            ctx.record_issues(issues)
            _search_again(ctx, issues)
            card = draft_card(ctx)
            card.llm_trace.retries = ctx.retries
            issues = verify_card(card, ctx)
            ctx.record_issues(issues)
            log_step(client_id, event_id, "VERIFY", {"issues": issues, "retry": ctx.retries})
        finish = "проверка пройдена"
        if issues:
            finish = "истекло время цикла" if _expired(started) else "достигнут лимит повторов"
            card = assemble(ctx)
            card.llm_trace.retries = ctx.retries
            card.missing_data = [
                *card.missing_data,
                "После двух проверок черновик с неподтверждёнными числами или цитатой отклонён.",
            ]
            issues = verify_card(card, ctx)
            finish += ": неподтверждённые утверждения заменены шаблоном"
        if issues:
            raise RuntimeError(f"Карточка не прошла проверку: {issues}")
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        ctx.finish_reason = finish
        card.llm_trace.latency_ms = elapsed_ms
        log_step(client_id, event_id, "FINALIZE", {"retries": ctx.retries, "elapsed_ms": elapsed_ms, "reason": finish})
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
            report = _invoke(ctx, name, {"client_id": ctx.client_id})
            profile = None if report["status"] == "error" else get_profile(ctx.client_id)
            if profile is not None:
                ctx.remember_profile(profile)
        elif name == "search_news":
            _keep_news(ctx, _invoke(ctx, name, {"query": ctx.event_id}))
        elif name == "search_bank_analytics":
            assert ctx.news is not None
            query = bank_query(ctx.news.event_type, ctx.news.title)
            _apply_bank(ctx, _invoke(ctx, name, {"query": query}))
        elif name == "get_counterparties":
            _invoke(ctx, name, {"client_id": ctx.client_id})
            fresh = get_profile(ctx.client_id)
            ctx.remember_counterparties([] if fresh is None else list(fresh.counterparties))
    if ctx.news is not None and ctx.news.id.startswith("pasted-") and ctx.plan is not None:
        if ctx.plan.provider in {"local", "cloud"}:
            ctx.extracted_fact = extract_fact(ctx.news)
    log_step(ctx.client_id, ctx.event_id, "GATHER", {"reports": _public_reports(ctx.tool_reports)})


def _search_again(ctx: AgentContext, issues: list[str]) -> str:
    reason = _search_reason(ctx, issues)
    query = _target_query(ctx, issues)
    if query is None:
        reason = f"{reason} Повтор с теми же аргументами не выполняется."
    ctx.record_search_reason(reason)
    log_step(ctx.client_id, ctx.event_id, "REVISE", {"retry": ctx.retries, "issues": issues, "reason": reason})
    if query is None:
        return reason
    tool = "search_news" if any(issue.startswith("number:") for issue in issues) else "search_bank_analytics"
    before = len(ctx.tool_reports)
    report = _invoke(ctx, tool, {"query": query})
    if tool == "search_news":
        _keep_news(ctx, report)
    else:
        _apply_bank(ctx, report)
    log_step(
        ctx.client_id,
        ctx.event_id,
        "GATHER",
        {"reason": reason, "reports": _public_reports(ctx.tool_reports[before:])},
    )
    return reason


def _invoke(ctx: AgentContext, name: str, arguments: dict) -> dict:
    signature = json.dumps({"tool": name, **arguments}, ensure_ascii=False, sort_keys=True)
    if signature in ctx.calls:
        message = "повторный вызов с теми же аргументами пропущен"
        ctx.record_tool(name, "skipped", message)
        return {"tool": name, "status": "skipped", "message": message}
    ctx.calls.add(signature)
    try:
        payload = run_tool(name, arguments)
    except CorpusError as error:
        ctx.record_tool(name, "error", str(error))
        return {"tool": name, "status": "error", "message": str(error)}
    status = "ok"
    message = ""
    if isinstance(payload, dict):
        message = str(payload.get("message") or "")
        if payload.get("status") == "none":
            status = "none"
    elif isinstance(payload, list) and not payload:
        status = "none"
        message = "Поиск не нашёл записей."
    ctx.record_tool(name, status, message)
    return {"tool": name, "status": status, "message": message, "payload": payload}


def _keep_news(ctx: AgentContext, report: dict) -> None:
    for hit in _records(report.get("payload")):
        item = find_news(str(hit.get("id") or hit.get("evidence_id") or ""))
        if item is not None:
            ctx.remember_news(item, primary=item.id == ctx.event_id)
    if ctx.news is None:
        item = find_news(ctx.event_id)
        if item is not None:
            ctx.remember_news(item, primary=True)


def _apply_bank(ctx: AgentContext, report: dict) -> None:
    if report["status"] == "skipped":
        return
    if report["status"] == "error":
        ctx.drop_bank(report["message"])
        return
    payload = report.get("payload")
    if not isinstance(payload, dict):
        return
    hits = _records(payload)
    note = str(payload.get("message") or "") or None
    if report["status"] == "none" or not hits:
        ctx.drop_bank(note or "")
        return
    for index, hit in enumerate(hits):
        document = find_bank(str(hit.get("id") or ""), hit.get("page"))
        if document is not None:
            ctx.remember_bank(document, note, primary=index == 0)


def _records(payload: object) -> list:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    if isinstance(payload, list):
        return payload
    return []


def _search_reason(ctx: AgentContext, issues: list[str]) -> str:
    numbers = [issue.split(":", 1)[1] for issue in issues if issue.startswith("number:")]
    if numbers:
        return f"В черновике есть неподтверждённое число {numbers[0]}: повторный поиск по нему."
    if "quote" in issues:
        return "Цитата не найдена в материале банка: повторный поиск по заголовку новости."
    if "page" in issues:
        return "Страница цитаты не совпала с материалом банка: повторный поиск по заголовку новости."
    if "conflict" in issues:
        return "Источники противоречат друг другу: повторный поиск по заголовку новости."
    if any(issue.startswith("source:") or issue.startswith("profile:") for issue in issues):
        return "В карточке есть источник или факт о компании, которого нет в контексте."
    if any(issue.startswith("layer:") or issue.startswith("evidence:") for issue in issues):
        return "Факт и вывод смешаны или у утверждения нет доказательства."
    if ctx.news is not None and ctx.bank is None and has_bank_topic(ctx.news.event_type):
        return "Поиск банка не дал материала: повторный поиск по заголовку новости."
    return "Не хватает подтверждения: повторный поиск."


def _target_query(ctx: AgentContext, issues: list[str]) -> str | None:
    if ctx.news is None:
        return None
    numbers = [issue.split(":", 1)[1] for issue in issues if issue.startswith("number:")]
    candidates = numbers or [ctx.news.title, bank_query(ctx.news.event_type, ctx.news.title)]
    tool = "search_news" if numbers else "search_bank_analytics"
    for query in candidates:
        signature = json.dumps({"tool": tool, "query": query}, ensure_ascii=False, sort_keys=True)
        if query and signature not in ctx.calls:
            return query
    return None


def _public_reports(reports: list[dict]) -> list[dict]:
    return [{"tool": item["tool"], "status": item["status"], "message": item["message"]} for item in reports]


def _expired(started: float) -> bool:
    return (time.perf_counter() - started) >= time_budget_s()
