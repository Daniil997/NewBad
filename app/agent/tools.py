from pathlib import Path

import yaml

from app.agent.pasted import pasted_by_id
from app.corpus.search import NewsItem, load_bank_documents, load_news, search_bank_analytics, search_news
from app.db import ROOT
from app.profile.build import ClientProfile, get_profile
from app.tracing.journal import log_tool_call

_UP = ("рост", "повыш", "выше", "ускор", "увелич")
_DOWN = ("сниж", "ниже", "замедл", "паден", "сокращ", "дефляц")
_NONE = "Нет релевантных материалов банка по этому запросу."

ALLOWED_TOOLS = ("get_profile", "search_news", "search_bank_analytics", "get_counterparties")


def run_tool(name: str, arguments: dict) -> object:
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"Инструмент не из белого списка: {name}")
    if name == "get_profile":
        profile = get_profile(str(arguments["client_id"]))
        result = None if profile is None else profile.model_dump()
    elif name == "search_news":
        result = _search_news(str(arguments["query"]))
    elif name == "search_bank_analytics":
        result = bank_analytics(str(arguments["query"]))
    elif name == "get_counterparties":
        profile = get_profile(str(arguments["client_id"]))
        result = [] if profile is None else [item.model_dump() for item in profile.counterparties]
    else:
        result = None
    log_tool_call(name, arguments, result)
    return result


def bank_query(event_type: str | None, title: str) -> str:
    taxonomy = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))
    spec = taxonomy["event_types"].get(event_type or "", {})
    topics = spec.get("bank_topics") or []
    if "key_rate" in topics:
        return "ключевая ставка"
    return title


def event_exposures(event_type: str | None) -> list[str]:
    taxonomy = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))
    spec = taxonomy["event_types"].get(event_type or "", {})
    return list(spec.get("exposures") or [])


def has_bank_topic(event_type: str | None) -> bool:
    taxonomy = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))
    spec = taxonomy["event_types"].get(event_type or "", {})
    return bool(spec.get("bank_topics"))


def matched_exposures(profile: ClientProfile, event_type: str | None) -> list[str]:
    owned = {item.id for item in profile.exposures}
    return [item for item in event_exposures(event_type) if item in owned]


def find_news(event_id: str) -> NewsItem | None:
    for item in load_news():
        if item.id == event_id:
            return item
    return pasted_by_id(event_id)


def _search_news(query: str) -> list[dict]:
    exact = find_news(query)
    if exact is not None:
        return [
            {
                "id": exact.id,
                "title": exact.title,
                "excerpt": exact.excerpt,
                "event_type": exact.event_type,
                "url": exact.url,
            }
        ]
    return [hit.model_dump() for hit in search_news(query)]


def bank_analytics(query: str, folder: Path | None = None) -> dict:
    """Цитата, документ и страница. Пустой поиск — явное сообщение, а не пустой список. Ошибка корпуса не маскируется."""
    hits = [
        {
            "id": hit.id,
            "title": hit.title,
            "published_at": hit.published_at,
            "page": hit.page,
            "source_type": hit.source_type,
            "quote": hit.excerpt,
            "strength": hit.strength,
        }
        for hit in search_bank_analytics(query, folder=folder)
    ]
    if not hits:
        return {"status": "none", "message": _NONE, "hits": []}
    message = _conflict(hits)
    return {"status": "conflict" if message else "found", "message": message, "hits": hits}


def find_bank(doc_id: str, page: int | None = None):
    for document in load_bank_documents():
        if document.doc_id != doc_id:
            continue
        if page is not None and document.page != page:
            continue
        return document
    return None


def _conflict(hits: list[dict]) -> str | None:
    chosen: list[dict] = []
    seen: set[str] = set()
    for hit in hits:
        if hit["id"] in seen:
            continue
        seen.add(hit["id"])
        chosen.append(hit)
    if len(chosen) < 2:
        return None
    notes: list[str] = []
    if not _dates_agree([str(item["published_at"] or "") for item in chosen]):
        listed = "; ".join(f"{item['title']} от {item['published_at']}, стр. {item['page']}" for item in chosen)
        notes.append(f"Даты источников различаются: {listed}.")
    leans = {_lean(str(item["quote"])) for item in chosen}
    if "up" in leans and "down" in leans:
        notes.append("Выводы источников расходятся: есть и рост, и снижение.")
    if not notes:
        return None
    return " ".join(notes)


def _dates_agree(dates: list[str]) -> bool:
    first = dates[0]
    return all(_same_period(first, other) for other in dates[1:])


def _same_period(left: str, right: str) -> bool:
    if left == right:
        return True
    if not left or not right or left == "unknown" or right == "unknown":
        return False
    return left.startswith(f"{right}-") or right.startswith(f"{left}-")


def _lean(quote: str) -> str | None:
    folded = quote.lower().replace("ё", "е")
    up = any(stem in folded for stem in _UP)
    down = any(stem in folded for stem in _DOWN)
    if up and not down:
        return "up"
    if down and not up:
        return "down"
    return None
