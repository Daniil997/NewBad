import json
import logging
import os
import re
import time

import httpx
import yaml

from app.db import ROOT
from app.llm.placement import request_options
from app.llm.registry import api_model, require_allowed
from app.tracing.journal import log_llm_call

logger = logging.getLogger(__name__)

_CLOUD_MODES = frozenset({"cloud_only", "hybrid"})
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
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


def post_chat(
    url: str,
    payload: dict,
    timeout: float,
    headers: dict[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    with httpx.Client(timeout=timeout, transport=transport) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


def complete(
    messages: list[dict[str, str]],
    *,
    model: str,
    mode: str,
    max_tokens: int,
    provider: str | None = None,
) -> Completion:
    chosen = _provider(mode, provider)
    if mode == "replay":
        raise LLMError("replay не ходит в сеть")
    if chosen == "cloud":
        try:
            model = api_model(model)
        except ValueError:
            raise LLMError("модель не разрешена для облачного сервиса") from None
        _require_cloud(mode)
        base = _cloud_base()
        headers = {"Authorization": f"Bearer {_cloud_key()}"}
        options = None
        reason = "облачный вызов"
    else:
        require_allowed(model)
        if model == "mock-model" or mode == "mock":
            raise LLMError("заглушка не ходит в сеть")
        base = _local_base()
        headers = None
        options = request_options()
        reason = "локальный вызов"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": min(max_tokens, _token_cap()),
    }
    if chosen == "cloud":
        payload["response_format"] = {"type": "json_object"}
    elif "11434" in base:
        # Ollama's thinking models may spend the output budget on reasoning and
        # return an empty message.content, which this client correctly rejects.
        # Disable thinking using Ollama's option and the OpenAI-compatible parameter.
        payload["think"] = False
        payload["reasoning_effort"] = "none"
    if options:
        payload["options"] = options

    # Ollama's OpenAI-compatible endpoint can ignore the `think` flag for
    # some thinking models. Use its native API so `think: false` is honored.
    ollama_native = chosen != "cloud" and "11434" in base
    if ollama_native:
        native_options = {"temperature": 0.2, "num_predict": payload["max_tokens"]}
        if options:
            native_options.update(options)
        request_payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": native_options,
        }
        url = f"{base.removesuffix('/v1')}/api/chat"
    else:
        request_payload = payload
        url = f"{base}/chat/completions"
    timeout = _timeout(chosen, options)
    started = time.perf_counter()
    logger.info(
        "llm_request_start provider=%s mode=%s model=%s endpoint=%s max_tokens=%s timeout_s=%s options_keys=%s",
        chosen, mode, model, url.split("?", 1)[0], payload["max_tokens"], timeout,
        sorted(options) if options else [],
    )
    if _content_logging_enabled():
        logger.info(
            "llm_prompt provider=%s mode=%s model=%s messages:\n%s",
            chosen, mode, model, json.dumps(messages, ensure_ascii=False, indent=2),
        )
    try:
        body = post_chat(url, request_payload, timeout, headers)
    except httpx.TimeoutException:
        logger.warning("llm_request_failed category=timeout provider=%s mode=%s model=%s endpoint=%s elapsed_ms=%s timeout_s=%s",
                       chosen, mode, model, url.split("?", 1)[0], int((time.perf_counter() - started) * 1000), timeout)
        raise LLMError("истекло время ожидания модели") from None
    except httpx.HTTPStatusError as exc:
        logger.warning("llm_request_failed category=http_status provider=%s mode=%s model=%s endpoint=%s status_code=%s elapsed_ms=%s",
                       chosen, mode, model, url.split("?", 1)[0], exc.response.status_code,
                       int((time.perf_counter() - started) * 1000))
        raise LLMError(_status_message(exc.response.status_code, chosen)) from None
    except httpx.HTTPError as exc:
        logger.warning("llm_request_failed category=transport provider=%s mode=%s model=%s endpoint=%s error_type=%s elapsed_ms=%s",
                       chosen, mode, model, url.split("?", 1)[0], type(exc).__name__,
                       int((time.perf_counter() - started) * 1000))
        raise LLMError("сервер модели недоступен") from None
    except json.JSONDecodeError:
        logger.warning("llm_request_failed category=invalid_json provider=%s mode=%s model=%s endpoint=%s elapsed_ms=%s",
                       chosen, mode, model, url.split("?", 1)[0], int((time.perf_counter() - started) * 1000))
        raise LLMError("модель вернула некорректный ответ") from None
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        if ollama_native:
            message = body["message"]
            text = message["content"]
            first = {"finish_reason": body.get("done_reason")}
            native_usage = body.get("usage") or {}
            tokens = int(native_usage.get("prompt_eval_count", 0) or 0) + int(
                native_usage.get("eval_count", 0) or 0
            )
            usage = {
                "prompt_tokens": native_usage.get("prompt_eval_count", 0),
                "completion_tokens": native_usage.get("eval_count", 0),
                "total_tokens": tokens,
            }
        else:
            choices = body["choices"]
            first = choices[0]
            message = first["message"]
            text = message["content"]
            usage = body.get("usage") or {}
            tokens = int(usage.get("total_tokens") or 0)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning(
            "llm_request_failed category=invalid_response_shape provider=%s mode=%s model=%s endpoint=%s elapsed_ms=%s body_type=%s top_level_keys=%s",
            chosen, mode, model, url.split("?", 1)[0], latency_ms, type(body).__name__,
            sorted(body.keys()) if isinstance(body, dict) else [],
        )
        raise LLMError("модель вернула некорректный ответ") from None
    if _content_logging_enabled():
        logger.info(
            "llm_response provider=%s mode=%s model=%s endpoint=%s content:\n%s",
            chosen, mode, model, url.split("?", 1)[0], text if isinstance(text, str) else repr(text),
        )
    if not isinstance(text, str) or not text.strip():
        logger.warning(
            "llm_request_failed category=empty_content provider=%s mode=%s model=%s endpoint=%s elapsed_ms=%s finish_reason=%s usage=%s message_keys=%s content_type=%s content_length=%s",
            chosen, mode, model, url.split("?", 1)[0], latency_ms, first.get("finish_reason"),
            usage, sorted(message.keys()) if isinstance(message, dict) else [],
            type(text).__name__, len(text) if isinstance(text, str) else -1,
        )
        raise LLMError("модель вернула некорректный ответ")
    logger.info(
        "llm_request_success provider=%s mode=%s model=%s endpoint=%s elapsed_ms=%s tokens=%s content_length=%s finish_reason=%s",
        chosen, mode, model, url.split("?", 1)[0], latency_ms, tokens, len(text), first.get("finish_reason"),
    )
    log_llm_call(model, chosen, mode, tokens=tokens, latency_ms=latency_ms, switch_reason=reason)
    return Completion(text=text, tokens=tokens, model=model, provider=chosen, mode=mode)


def parse_json(text: str) -> dict:
    cleaned = _FENCE.sub("", text.strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        payload = _first_json_object(cleaned)
        if payload is None:
            raise LLMError("модель вернула не JSON") from exc
    if not isinstance(payload, dict):
        raise LLMError("модель вернула не объект")
    return payload


def _first_json_object(text: str) -> dict | None:
    """Accept a single JSON object wrapped in harmless model prose or code fences."""
    decoder = json.JSONDecoder()
    offset = 0
    while True:
        start = text.find("{", offset)
        if start < 0:
            return None
        try:
            value, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            offset = start + 1
            continue
        if isinstance(value, dict):
            return value
        offset = start + 1


def server_reachable() -> bool:
    base = _local_base()
    root = base.removesuffix("/v1")
    if _status_ok(f"{root}/health"):
        return True
    if "11434" in root:
        return _status_ok(f"{root}/api/version")
    return False


def _content_logging_enabled() -> bool:
    """Log full prompts and generated text in development, unless explicitly disabled."""
    configured = os.environ.get("LLM_LOG_CONTENT")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return os.environ.get("APP_ENV", "development").strip().lower() != "production"


def _provider(mode: str, provider: str | None) -> str:
    if provider in {"local", "cloud"}:
        return provider
    if mode == "cloud_only":
        return "cloud"
    return "local"


def _require_cloud(mode: str) -> None:
    if os.environ.get("ALLOW_CLOUD", "false").lower() != "true":
        raise LLMError("облако выключено")
    if mode not in _CLOUD_MODES:
        raise LLMError("облачный вызов разрешён только в облачном режиме")


def _cloud_base() -> str:
    expanded = _provider_value("cloud", "base_url")
    explicit = os.environ.get("CASE_LLM_BASE_URL", "").strip()
    base = explicit or expanded
    if not base:
        raise LLMError("не задан адрес облачного сервиса")
    return base.rstrip("/")


def _cloud_key() -> str:
    key = os.environ.get("CASE_LLM_API_KEY", "").strip()
    if not key:
        raise LLMError("не задан ключ облачного сервиса")
    return key


def _local_base() -> str:
    explicit = os.environ.get("LOCAL_LLM_BASE_URL", "").strip()
    base = explicit or _provider_value("local", "base_url") or "http://127.0.0.1:8080/v1"
    return base.rstrip("/")


def _provider_value(name: str, field: str) -> str:
    loaded = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text(encoding="utf-8")) or {}
    spec = (loaded.get("providers") or {}).get(name) or {}
    return _expand(spec.get(field, ""))


def _expand(value: object) -> str:
    def repl(match: re.Match[str]) -> str:
        current = os.environ.get(match.group(1))
        if current is None or not current.strip():
            return match.group(2) or ""
        return current

    return _PLACEHOLDER.sub(repl, str(value or "")).strip()


def _token_cap() -> int:
    table = (_config_limits().get("max_tokens") or {})
    values = [int(item) for item in table.values()]
    return max(values) if values else 400


def _config_limits() -> dict:
    loaded = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text(encoding="utf-8")) or {}
    limits = loaded.get("limits") or {}
    return limits if isinstance(limits, dict) else {}


def _timeout(provider: str, options: dict | None) -> float:
    raw = os.environ.get("LLM_TIMEOUT_S")
    if raw is None:
        limit = float(_config_limits().get("timeout_s", 90))
    else:
        try:
            limit = float(raw)
        except ValueError:
            limit = 90.0
    if provider == "local" and options is not None and "num_gpu" not in options:
        return max(limit, 600.0)
    return limit


def _status_message(code: int, provider: str) -> str:
    if code in {401, 403}:
        if provider == "cloud":
            return "облачный сервис отклонил доступ"
        return "сервер модели отклонил доступ"
    if code in {402, 429}:
        return "превышен лимит запросов или бюджет"
    return "сервер модели недоступен"


def _status_ok(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=0.4)
    except httpx.HTTPError:
        return False
    return response.status_code == 200
