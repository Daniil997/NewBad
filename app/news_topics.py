"""Company-independent news topics, assigned once at import and stored with the article."""

import yaml

from app.db import ROOT


def load_taxonomy() -> dict:
    return yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))


def _fold(text: str) -> str:
    return text.lower().replace("ё", "е")


def classify(text: str, taxonomy: dict | None = None) -> str:
    """Точные триггеры задают категорию. При равном числе совпадений или без свидетельств — «неизвестно»."""
    payload = taxonomy or load_taxonomy()
    rules = payload["event_types"]
    settings = payload.get("classification") or {}
    unknown = str(settings.get("unknown") or "unknown")
    min_broad = int(settings.get("min_broad_hits") or 2)
    haystack = _fold(text)
    candidates: list[tuple[int, int, str]] = []
    for event_type, spec in rules.items():
        if event_type == unknown:
            continue
        specific = 0
        broad = 0
        for trigger in spec.get("triggers") or []:
            kind = _trigger_hit(trigger, haystack)
            if kind == "broad":
                broad += 1
            elif kind == "specific":
                specific += 1
        if specific == 0 and broad < min_broad:
            continue
        candidates.append((specific, int(spec.get("priority") or 0), event_type))
    if not candidates:
        return unknown
    candidates.sort(reverse=True)
    best_hits, _priority, best_id = candidates[0]
    if len(candidates) > 1 and candidates[1][0] == best_hits:
        return unknown
    return best_id


def _trigger_hit(trigger: str | dict, haystack: str) -> str | None:
    """specific — точный триггер, broad — общий, None — совпадения нет."""
    if isinstance(trigger, str):
        return "specific" if _fold(trigger) in haystack else None
    kind = "broad" if trigger.get("broad") else "specific"
    parts = trigger.get("all")
    if isinstance(parts, list):
        folded = [_fold(str(part)) for part in parts]
        if folded and all(part and part in haystack for part in folded):
            return kind
        return None
    phrase = _fold(str(trigger.get("phrase") or trigger.get("text") or ""))
    if phrase and phrase in haystack:
        return kind
    return None


def news_topic(title: str, text: str, taxonomy: dict | None = None) -> tuple[str, str]:
    """Prefer a clear headline topic; use the body only when the headline is inconclusive.

    Unknown/ambiguous is retained internally, with a neutral public label rather
    than a guessed topic. No profile or model route participates in this decision.
    """
    payload = taxonomy or load_taxonomy()
    unknown = str((payload.get("classification") or {}).get("unknown") or "unknown")
    event_type = classify(title, payload)
    if event_type == unknown:
        event_type = classify(f"{title} {text}", payload)
    label = payload["event_types"].get(event_type, {}).get("title")
    return event_type, str(label) if event_type != unknown and label else "Новости бизнеса"
