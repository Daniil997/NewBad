import hashlib
import json
import logging
import os
import time as monotonic_time
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

import yaml
from pydantic import BaseModel

from app.db import ROOT, connect, init_db
from app.llm.client import LLMError, complete, parse_json, server_reachable
from app.llm.registry import api_models, require_allowed
from app.tracing.journal import log_llm_call
from app.user_store import developer_settings

logger = logging.getLogger(__name__)

ALLOWED_TOOLS = ("get_profile", "search_news", "search_bank_analytics", "get_counterparties")


class PlanChoice(BaseModel):
    tools: list[str]
    model: str
    provider: str
    mode: str
    tokens: int = 0


class Route(BaseModel):
    mode: str
    provider: str
    model: str
    reason: str = ""


@dataclass
class TierResult:
    route: Route
    text: str | None = None
    tokens: int = 0


def current_route() -> Route:
    return resolve_route("light")


def resolve_route(tier: str = "light", *, task: str | None = None, space: str = "demo") -> Route:
    if task:
        selection = developer_settings().get("task_routes", {}).get(space, {}).get(task, "mock")
        return _task_route("mock" if selection == "inherit" else selection)
    mode = _mode_name()
    if _is_replay(mode):
        model = _first_model(mode, tier)
        return Route(mode="replay", provider="replay", model=model, reason="replay читает кэш и не ходит в сеть")
    picked = _first_open(mode, tier)
    if picked is not None:
        return picked
    return Route(mode="mock", provider="mock", model="mock-model", reason=_closed_reason())


def choose_tools(planned: list[str]) -> PlanChoice:
    """Один короткий вызов: модель может только сузить уже собранный кодом список."""
    tools = [name for name in planned if name in ALLOWED_TOOLS]
    outcome = complete_tier(
        [
            {
                "role": "system",
                "content": (
                    "Верни только JSON {\"tools\": [...]} . "
                    "Оставь инструменты из переданного списка, ничего другого не добавляй."
                ),
            },
            {"role": "user", "content": json.dumps({"tools": tools}, ensure_ascii=False)},
        ],
        tier="light",
        max_tokens=token_limit("plan"),
    )
    if not outcome.text:
        return _plan(tools, outcome.route, 0)
    try:
        chosen = parse_json(outcome.text).get("tools") or []
    except LLMError:
        _log(outcome.route.model, outcome.route.provider, outcome.route.mode, "ответ модели не список инструментов")
        return _plan(tools, outcome.route, outcome.tokens)
    kept = [name for name in chosen if name in tools]
    return _plan(kept or tools, outcome.route, outcome.tokens)


def complete_tier(messages: list[dict[str, str]], *, tier: str, max_tokens: int, task: str | None = None, space: str = "demo") -> TierResult:
    if task:
        configured = developer_settings().get("task_routes", {}).get(space, {}).get(task, "mock")
        return _complete_task_route(messages, "mock" if configured == "inherit" else configured, max_tokens)
    mode = _mode_name()
    if _is_replay(mode):
        return _from_cache(messages, tier)
    tried_cloud = False
    handoff = ""
    for entry in _entries(mode, tier):
        provider = str(entry.get("provider") or "")
        model = str(entry.get("model") or "")
        if provider == "cloud":
            if tried_cloud:
                continue
            tried_cloud = True
            blocked = _cloud_block()
            if blocked:
                handoff = blocked
                continue
            try:
                result = complete(messages, model=model, mode=mode, max_tokens=max_tokens, provider="cloud")
            except LLMError as exc:
                handoff = f"облачный вызов не удался ({exc}): переход на локальную модель из конфигурации"
                continue
            _remember(messages, result.model, result.provider, result.text, result.tokens)
            route = Route(mode=mode, provider="cloud", model=result.model, reason="облачный маршрут из конфигурации")
            return TierResult(route, result.text, result.tokens)
        if provider == "local":
            if handoff:
                _log(model, "local", mode, handoff)
            try:
                result = complete(messages, model=model, mode=mode, max_tokens=max_tokens, provider="local")
            except LLMError as exc:
                _log("mock-model", "mock", "mock", str(exc))
                route = Route(mode="mock", provider="mock", model="mock-model", reason=str(exc))
                return TierResult(route)
            _remember(messages, result.model, result.provider, result.text, result.tokens)
            route = Route(mode=mode, provider="local", model=result.model, reason="локальный маршрут из конфигурации")
            return TierResult(route, result.text, result.tokens)
        reason = "режим mock, модель не вызывается"
        route = Route(mode="mock", provider="mock", model=model or "mock-model", reason=reason)
        _log(route.model, route.provider, route.mode, reason)
        return TierResult(route)
    reason = handoff or _closed_reason()
    _log("mock-model", "mock", "mock", reason)
    return TierResult(Route(mode="mock", provider="mock", model="mock-model", reason=reason))


def _complete_task_route(messages: list[dict[str, str]], selection: str, max_tokens: int) -> TierResult:
    route = _task_route(selection)
    started = monotonic_monotonic_time.perf_counter()
    logger.info(
        "llm_task_route_start task_selection=%s mode=%s provider=%s model=%s max_tokens=%s reason=%s",
        selection, route.mode, route.provider, route.model, max_tokens, route.reason,
    )
    if route.provider == "mock":
        logger.warning(
            "llm_task_route_mock task_selection=%s mode=%s provider=%s model=%s reason=%s",
            selection, route.mode, route.provider, route.model, route.reason,
        )
        _log(route.model, route.provider, route.mode, route.reason)
        return TierResult(route)
    provider, mode, model = route.provider, route.mode, route.model
    try:
        result = complete(messages, model=model, mode=mode, max_tokens=max_tokens, provider=provider)
    except LLMError as exc:
        elapsed_ms = int((monotonic_time.perf_counter() - started) * 1000)
        reason = f"Выбранная модель недоступна ({exc})"
        logger.warning(
            "llm_task_route_failed task_selection=%s mode=%s provider=%s model=%s elapsed_ms=%s error=%s",
            selection, mode, provider, model, elapsed_ms, str(exc),
        )
        _log("mock-model", "mock", "mock", reason)
        return TierResult(Route(mode="mock", provider="mock", model="mock-model", reason=reason))
    elapsed_ms = int((monotonic_time.perf_counter() - started) * 1000)
    logger.info(
        "llm_task_route_success task_selection=%s mode=%s provider=%s model=%s elapsed_ms=%s tokens=%s content_length=%s",
        selection, mode, provider, result.model, elapsed_ms, result.tokens, len(result.text),
    )
    _remember(messages, result.model, result.provider, result.text, result.tokens)
    return TierResult(Route(mode=mode, provider=provider, model=result.model, reason="Маршрут модели для выбранной задачи"), result.text, result.tokens)


def _task_route(selection: str) -> Route:
    if selection == "mock":
        return Route(mode="mock", provider="mock", model="mock-model", reason="Для задачи выбран mock; модель не вызывается")
    local_options = {"local_gemma": "gemma-4-26b-a4b", "local_qwen": "qwen3.6-35b-a3b"}
    cloud_options = {
        "cloud_gemma": "google/gemma-4-26b-a4b-it",
        "cloud_glm": "z-ai/glm-5.3-flash",
        "cloud_qwen": "qwen/qwen3.6-35b-a3b",
        "cloud_deepseek": "deepseek/deepseek-v4.1-flash",
        "cloud_vl": "qwen/qwen3-vl-30b-a3b-instruct",
        "cloud_kimi": "moonshotai/kimi-k2.6",
    }
    if selection in local_options:
        model = local_options[selection]
        require_allowed(model)
        return Route(mode="local_only", provider="local", model=model, reason="Маршрут модели для выбранной задачи")
    if selection in cloud_options:
        model = cloud_options[selection]
        if model not in api_models():
            return Route(mode="mock", provider="mock", model="mock-model", reason="Облачная модель отсутствует в allowlist")
        blocked = _cloud_block()
        if blocked:
            return Route(mode="mock", provider="mock", model="mock-model", reason=blocked)
        return Route(mode="cloud_only", provider="cloud", model=model, reason="Маршрут модели для выбранной задачи")
    return Route(mode="mock", provider="mock", model="mock-model", reason="Неизвестная задача-модель; безопасная заглушка")


def local_status() -> str:
    route = current_route()
    if route.provider != "local":
        return "not_used"
    return "up" if server_reachable() else "down"


def max_retries() -> int:
    return int(_limits().get("max_retries", 2))


def time_budget_s() -> float:
    return float(_limits().get("timeout_s", 90))


def token_limit(kind: str) -> int:
    table = _limits().get("max_tokens") or {}
    if kind not in table:
        raise KeyError(kind)
    return int(table[kind])


def budget_policy() -> dict:
    limits = _limits()
    return {
        "provider_daily_limit_rub": int(limits["provider_daily_limit_rub"]),
        "daily_budget_rub": int(limits["daily_budget_rub"]),
        "reset": str(limits["budget_reset"]),
        "tz": str(limits["budget_reset_tz"]),
        "rub_per_1k_tokens": float(limits["rub_per_1k_tokens"]),
    }


def _from_cache(messages: list[dict[str, str]], tier: str) -> TierResult:
    cached = _recall(messages)
    if cached is None:
        reason = "replay: записи в кэше нет"
        route = Route(mode="replay", provider="replay", model=_first_model("replay", tier), reason=reason)
        _log(route.model, "replay", "replay", reason)
        return TierResult(route)
    reason = "replay: ответ взят из кэша"
    route = Route(mode="replay", provider=cached["provider"], model=cached["model"], reason=reason)
    _log(route.model, route.provider, "replay", reason)
    return TierResult(route, cached["response"], int(cached["tokens"]))


def _first_open(mode: str, tier: str) -> Route | None:
    blocked = _cloud_block()
    for entry in _entries(mode, tier):
        provider = str(entry.get("provider") or "")
        model = str(entry.get("model") or "")
        if provider == "cloud":
            if blocked:
                continue
            return Route(mode=mode, provider="cloud", model=model, reason="облачный маршрут из конфигурации")
        if provider == "local":
            return Route(mode=mode, provider="local", model=model, reason="локальный маршрут из конфигурации")
        if provider == "mock":
            return Route(
                mode="mock",
                provider="mock",
                model=model or "mock-model",
                reason="режим mock, модель не вызывается",
            )
    return None


def _cloud_block() -> str | None:
    if os.environ.get("ALLOW_CLOUD", "false").lower() != "true":
        return "облако выключено, ответ собирает заглушка"
    if not os.environ.get("CASE_LLM_API_KEY", "").strip():
        return "не задан ключ облачного сервиса"
    policy = budget_policy()
    rate = policy["rub_per_1k_tokens"]
    if rate > 0:
        spent = _cloud_spend_since_reset() / 1000 * rate
        if spent >= policy["daily_budget_rub"]:
            return "достигнут внутренний дневной бюджет облака"
    return None


def _cloud_spend_since_reset() -> int:
    """Tokens logged since 03:00 Moscow; ruble conversion needs the supplied tariff."""
    # Moscow has used UTC+3 without seasonal changes since 2014; avoid OS tzdata dependency.
    zone = timezone(timedelta(hours=3))
    local_now = datetime.now(zone)
    reset = datetime.combine(local_now.date(), time(3), tzinfo=zone)
    if local_now < reset:
        reset -= timedelta(days=1)
    cutoff = reset.astimezone(timezone.utc).isoformat()
    init_db()
    with connect() as connection:
        row = connection.execute(
            "SELECT COALESCE(SUM(tokens), 0) AS tokens FROM llm_calls "
            "WHERE provider = 'cloud' AND created_at >= ?",
            (cutoff,),
        ).fetchone()
    return int(row["tokens"])


def _closed_reason() -> str:
    return _cloud_block() or "маршрут не настроен, ответ собирает заглушка"


def _plan(tools: list[str], route: Route, tokens: int) -> PlanChoice:
    return PlanChoice(tools=tools, model=route.model, provider=route.provider, mode=route.mode, tokens=tokens)


def _log(model: str, provider: str, mode: str, reason: str) -> None:
    log_llm_call(model, provider, mode, switch_reason=reason)


def _remember(messages: list[dict[str, str]], model: str, provider: str, text: str, tokens: int) -> None:
    init_db()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO llm_replay (prompt_hash, model, provider, response, tokens, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(prompt_hash) DO UPDATE SET
                model = excluded.model,
                provider = excluded.provider,
                response = excluded.response,
                tokens = excluded.tokens,
                created_at = excluded.created_at
            """,
            (_hash(messages), model, provider, text, tokens),
        )


def _recall(messages: list[dict[str, str]]) -> dict | None:
    init_db()
    with connect() as connection:
        row = connection.execute(
            "SELECT model, provider, response, tokens FROM llm_replay WHERE prompt_hash = ?",
            (_hash(messages),),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def _hash(messages: list[dict[str, str]]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def known_modes() -> tuple[str, ...]:
    return tuple(str(name) for name in (_config().get("modes") or {}))


def _mode_name() -> str:
    mode = os.environ.get("LLM_MODE", "mock")
    if mode not in known_modes():
        return "mock"
    return mode


def _is_replay(mode: str) -> bool:
    spec = (_config().get("modes") or {}).get(mode) or {}
    return bool(spec.get("cache_only"))


def _entries(mode: str, tier: str) -> list[dict]:
    spec = (_config().get("modes") or {}).get(mode) or {}
    entries = ((spec.get("tiers") or {}).get(tier) or [])
    return [item for item in entries if isinstance(item, dict)]


def _first_model(mode: str, tier: str) -> str:
    entries = _entries(mode, tier)
    if not entries:
        return "mock-model"
    return str(entries[0].get("model") or "mock-model")


def _limits() -> dict:
    return _config().get("limits") or {}


def _config() -> dict:
    loaded = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}
