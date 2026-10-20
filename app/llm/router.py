import os

import yaml
from pydantic import BaseModel

from app.db import ROOT
from app.tracing.journal import log_llm_call

ALLOWED_TOOLS = ("get_profile", "search_news", "search_bank_analytics", "get_counterparties")


class PlanChoice(BaseModel):
    tools: list[str]
    model: str
    provider: str
    mode: str
    tokens: int = 0


def choose_tools(planned: list[str]) -> PlanChoice:
    """Место вызова модели. В mock возвращает тот же список инструментов и сеть не трогает."""
    mode = os.environ.get("LLM_MODE", "mock")
    allow_cloud = os.environ.get("ALLOW_CLOUD", "false").lower() == "true"
    tools = [name for name in planned if name in ALLOWED_TOOLS]
    if mode in {"hybrid", "cloud_only"} and not allow_cloud:
        log_llm_call("mock-model", "mock", "mock", switch_reason="облако выключено")
        return PlanChoice(tools=tools, model="mock-model", provider="mock", mode="mock")
    if mode != "mock":
        log_llm_call("mock-model", "mock", "mock", switch_reason="живая генерация начнётся на этапе 6")
        return PlanChoice(tools=tools, model="mock-model", provider="mock", mode="mock")
    log_llm_call("mock-model", "mock", "mock", tokens=0)
    return PlanChoice(tools=tools, model="mock-model", provider="mock", mode="mock")


def max_retries() -> int:
    return int(_limits().get("max_retries", 2))


def _limits() -> dict:
    raw = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text(encoding="utf-8"))
    return raw.get("limits") or {}
