import yaml

from app.db import ROOT


def api_models() -> tuple[str, ...]:
    raw = (_models().get("api") or [])
    return tuple(str(item) for item in raw)


def local_aliases() -> dict[str, str]:
    raw = _models().get("local") or {}
    return {str(name): str(target) for name, target in raw.items()}


def api_model(name: str) -> str:
    """Точный ID облачного API. Локальное имя переводится, чужое отклоняется до запроса."""
    canonical = local_aliases().get(name, name)
    if canonical not in set(api_models()):
        raise ValueError(f"Модель не из списка кейса: {name}")
    return canonical


def require_allowed(model: str) -> str:
    """Локальный сервер понимает только свои имена и заглушку, не облачные ID."""
    if model not in set(local_aliases()) | {"mock-model"}:
        raise ValueError(f"Модель не из списка кейса: {model}")
    return model


def _models() -> dict:
    return _config().get("models") or {}


def _config() -> dict:
    loaded = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}
