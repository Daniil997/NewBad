"""Почему модель не дала проверяемый ответ.

Реальный режим не умеет отвечать шаблоном, поэтому любой сбой модели превращается в 503.
Здесь запоминается причина последнего сбоя в текущем запросе, чтобы пользователь и
разработчик видели, что именно произошло: модель не вызывалась или ответ не прошёл проверку.
"""

from contextvars import ContextVar

_FAILURE: ContextVar[dict[str, str] | None] = ContextVar("generation_failure", default=None)


def reset_failure() -> None:
    _FAILURE.set(None)


def record_failure(kind: str, reason: str, *, mode: str = "", provider: str = "", model: str = "") -> None:
    """kind: model_unavailable | invalid_json | validation."""
    _FAILURE.set({"kind": kind, "reason": reason, "mode": mode, "provider": provider, "model": model})


def last_failure() -> dict[str, str] | None:
    return _FAILURE.get()


def failure_detail(default: str, retry_hint: str = "Попробуйте переформулировать вопрос.") -> str:
    """Текст для пользователя; без записанной причины возвращает прежнее сообщение."""
    failure = last_failure()
    if not failure:
        return default
    kind = failure["kind"]
    where = f"режим {failure['mode'] or '?'}, провайдер {failure['provider'] or '?'}"
    reason = failure["reason"] or "причина не указана"
    if kind == "model_unavailable":
        return (
            f"Модель не подключена или недоступна ({where}): {reason}. "
            "Проверьте LLM_MODE, ALLOW_CLOUD, CASE_LLM_API_KEY, локальный сервер "
            "и маршруты задач в настройках разработки."
        )
    if kind == "invalid_json":
        return f"Модель ответила не в формате JSON ({where}). {retry_hint}"
    if kind == "validation":
        return f"Модель ответила, но ответ не прошёл проверку по материалам: {reason}. {retry_hint}"
    return default
