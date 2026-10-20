import json
import os
import re
import time

import httpx

from app.llm.placement import request_options
from app.llm.registry import require_allowed
from app.tracing.journal import log_llm_call

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class LLMError(Exception):
    pass


class Completion:
    def __init__(self, text: str, tokens: int, model: str, provider: str, mode: str) -> None:
        self.text = text
        self.tokens = tokens
        self.model = model
        self.provider = provider
        self.mode = mode


def post_chat(url: str, payload: dict, timeout: float) -> dict:
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


def complete(messages: list[dict[str, str]], *, model: str, mode: str, max_tokens: int) -> Completion:
    require_allowed(model)
    if model == "mock-model":
        raise LLMError("заглушка не ходит в сеть")
    base = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
    url = f"{base}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    options = request_options()
    if options:
        payload["options"] = options
    options = request_options()
    if options:
        payload["options"] = options
    started = time.perf_counter()
    try:
        body = post_chat(url, payload, timeout=_timeout())
        text = body["choices"][0]["message"]["content"]
        tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError("сервер модели недоступен") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    log_llm_call(model, "local", mode, tokens=tokens, latency_ms=latency_ms)
    return Completion(text=str(text), tokens=tokens, model=model, provider="local", mode=mode)


def parse_json(text: str) -> dict:
    cleaned = _FENCE.sub("", text.strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMError("модель вернула не JSON") from exc
    if not isinstance(payload, dict):
        raise LLMError("модель вернула не объект")
    return payload


def server_reachable() -> bool:
    base = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
    root = base.removesuffix("/v1")
    if _status_ok(f"{root}/health"):
        return True
    if "11434" in root:
        return _status_ok(f"{root}/api/version")
    return False


def _status_ok(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=0.4)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _timeout() -> float:
    raw = os.environ.get("LLM_TIMEOUT_S", "90")
    try:
        limit = float(raw)
    except ValueError:
        limit = 90.0
    options = request_options()
    if options is not None and "num_gpu" not in options:
        return max(limit, 600.0)
    return limit
