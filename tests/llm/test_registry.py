import pytest
import yaml

from app.db import ROOT
from app.llm.registry import api_model, api_models, local_aliases, require_allowed
from app.llm.router import budget_policy, max_retries, time_budget_s, token_limit

INSTRUCTION = (
    "google/gemma-4-26b-a4b-it",
    "z-ai/glm-5.3-flash",
    "qwen/qwen3.6-35b-a3b",
    "deepseek/deepseek-v4.1-flash",
    "qwen/qwen3-vl-30b-a3b-instruct",
    "moonshotai/kimi-k2.6",
)


def test_api_catalog_matches_the_instruction():
    assert api_models() == INSTRUCTION


def test_local_names_map_to_api_ids_and_unknown_names_are_rejected():
    assert api_model("gemma-4-26b-a4b") == "google/gemma-4-26b-a4b-it"
    assert api_model("qwen3.6-35b-a3b") == "qwen/qwen3.6-35b-a3b"
    assert api_model("deepseek/deepseek-v4.1-flash") == "deepseek/deepseek-v4.1-flash"
    assert require_allowed("gemma-4-26b-a4b") == "gemma-4-26b-a4b"
    assert require_allowed("mock-model") == "mock-model"
    with pytest.raises(ValueError):
        api_model("gpt-4")
    with pytest.raises(ValueError):
        api_model("gemma4:26b-a4b-it-qat")
    with pytest.raises(ValueError):
        require_allowed("google/gemma-4-26b-a4b-it")
    assert set(local_aliases().values()) <= set(api_models())


def test_configured_routes_use_real_ids():
    loaded = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text(encoding="utf-8"))
    for spec in (loaded.get("modes") or {}).values():
        for entries in (spec.get("tiers") or {}).values():
            for entry in entries:
                provider = entry["provider"]
                model = entry["model"]
                if provider == "cloud":
                    assert model in api_models()
                elif provider == "local":
                    assert model in local_aliases()
                else:
                    assert model == "mock-model"


def test_budget_and_call_limits_follow_the_instruction():
    policy = budget_policy()
    assert policy["provider_daily_limit_rub"] == 500
    assert policy["daily_budget_rub"] == 400
    assert policy["daily_budget_rub"] < policy["provider_daily_limit_rub"]
    assert policy["reset"] == "03:00"
    assert policy["tz"] == "Europe/Moscow"
    assert policy["rub_per_1k_tokens"] == 0.0
    assert max_retries() == 2
    assert time_budget_s() == 90
    assert token_limit("plan") == 120
    assert token_limit("fact") == 180
    assert token_limit("copy") == 400
    assert token_limit("answer") == 250
