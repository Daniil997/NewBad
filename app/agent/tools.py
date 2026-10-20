import yaml

from app.agent.pasted import pasted_by_id
from app.corpus.search import NewsItem, load_bank_documents, load_news, search_bank_analytics, search_news
from app.db import ROOT
from app.profile.build import ClientProfile, get_profile
from app.tracing.journal import log_tool_call

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
        result = [hit.model_dump() for hit in search_bank_analytics(str(arguments["query"]))]
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


def find_bank(doc_id: str):
    for document in load_bank_documents():
        if document.doc_id == doc_id:
            return document
    return None
