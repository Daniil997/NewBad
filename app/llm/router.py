import json
import os

import yaml
from pydantic import BaseModel

from app.db import ROOT
from app.llm.client import LLMError, complete, parse_json, server_reachable
from app.llm.registry import require_allowed
from app.tracing.journal import log_llm_call

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


def current_route() -> Route:
    mode = os.environ.get("LLM_MODE", "mock")
    allow_cloud = os.environ.get("ALLOW_CLOUD", "false").lower() == "true"
    if mode in {"hybrid", "cloud_only", "replay"} and not allow_cloud:
        return Route(mode="mock", provider="mock", model="mock-model")
    if mode in {"local_only", "local_dual"}:
        model = _tier_model(mode, "light")
        require_allowed(model)
        return Route(mode=mode, provider="local", model=model)
    return Route(mode="mock", provider="mock", model="mock-model")


def choose_tools(planned: list[str]) -> PlanChoice:
    """Один короткий вызов: модель может только сузить уже собранный кодом список."""
    tools = [name for name in planned if name in ALLOWED_TOOLS]
    route = current_route()
    if route.provider != "local":
        if os.environ.get("LLM_MODE", "mock") == "mock":
            reason = "режим mock, модель не вызывается"
        else:
            reason = "облако выключено, ответ собирает заглушка"
        log_llm_call(route.model, route.provider, route.mode, switch_reason=reason)
        return PlanChoice(tools=tools, model=route.model, provider=route.provider, mode=route.mode)
    try:
        result = complete(
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
            model=route.model,
            mode=route.mode,
            max_tokens=120,
        )
        chosen = parse_json(result.text).get("tools") or []
        kept = [name for name in chosen if name in tools]
        return PlanChoice(
            tools=kept or tools,
            model=result.model,
            provider=result.provider,
            mode=result.mode,
            tokens=result.tokens,
        )
    except LLMError as exc:
        log_llm_call("mock-model", "mock", "mock", switch_reason=str(exc))
        return PlanChoice(tools=tools, model="mock-model", provider="mock", mode="mock")


def local_status() -> str:
    route = current_route()
    if route.provider != "local":
        return "not_used"
    return "up" if server_reachable() else "down"


def max_retries() -> int:
    return int(_limits().get("max_retries", 2))


def time_budget_s() -> float:
    return float(_limits().get("timeout_s", 90))


def _tier_model(mode: str, tier: str) -> str:
    modes = _config().get("modes") or {}
    entries = ((modes.get(mode) or {}).get("tiers") or {}).get(tier) or []
    if not entries:
        return "gemma-4-26b-a4b"
    return str(entries[0]["model"])


def _limits() -> dict:
    return _config().get("limits") or {}


def _config() -> dict:
    return yaml.safe_load((ROOT / "config" / "llm.yaml").read_text(encoding="utf-8"))
