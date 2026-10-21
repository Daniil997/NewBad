from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.pasted import pasted_by_id
from app.corpus.search import CorpusError, NewsItem, load_bank_documents, load_news, search_bank_analytics, search_news
from app.db import ROOT
from app.profile.build import ClientProfile, get_profile
from app.tracing.journal import log_tool_call

_UP = ("рост", "повыш", "выше", "ускор", "увелич")
_DOWN = ("сниж", "ниже", "замедл", "паден", "сокращ", "дефляц")
_NONE = "Нет релевантных материалов банка по этому запросу."
_PARAMS = {
    "get_profile": "client_id",
    "get_counterparties": "client_id",
    "search_news": "query",
    "search_bank_analytics": "query",
}

ALLOWED_TOOLS = ("get_profile", "search_news", "search_bank_analytics", "get_counterparties")


class Evidence(BaseModel):
    model_config = ConfigDict(extra="allow")

    evidence_id: str = Field(min_length=1)
    source: str = Field(min_length=1)


class ToolResult(BaseModel):
    tool: Literal["get_profile", "search_news", "search_bank_analytics", "get_counterparties"]
    status: Literal["ok", "none", "error"]
    evidence_id: str = ""
    source: str = Field(min_length=1)
    message: str = ""
    items: list[Evidence] = Field(default_factory=list)

    def model_post_init(self, _context: object) -> None:
        if self.status == "ok" and (not self.evidence_id or not self.items):
            raise ValueError("Успешный результат без доказательства")
        if self.status == "none" and self.items:
            raise ValueError("Пустой результат не должен содержать доказательства")


def run_tool(name: str, arguments: dict) -> dict:
    stored = arguments if isinstance(arguments, dict) else {}
    try:
        cleaned = _checked_arguments(name, arguments)
    except ValueError as error:
        log_tool_call(str(name), stored, {"status": "error", "message": str(error)})
        raise
    try:
        checked = ToolResult.model_validate(_call(name, cleaned)).model_dump()
    except CorpusError as error:
        log_tool_call(name, cleaned, {"status": "error", "message": str(error)})
        raise
    except (ValidationError, ValueError) as error:
        log_tool_call(name, cleaned, {"status": "error", "message": "Некорректный формат результата"})
        raise ValueError("Результат инструмента не прошёл проверку формата") from error
    log_tool_call(name, cleaned, checked)
    return checked


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
        hit = {
            "id": exact.id,
            "title": exact.title,
            "excerpt": exact.excerpt,
            "event_type": exact.event_type,
            "url": exact.url,
            "source_name": exact.source,
        }
        return [_evidence(hit, exact.id, _news_source(hit))]
    return [_evidence(hit.model_dump(), hit.id, _news_source(hit.model_dump())) for hit in search_news(query)]


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


def _checked_arguments(name: str, arguments: dict) -> dict:
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"Инструмент не из белого списка: {name}")
    if not isinstance(arguments, dict):
        raise ValueError("Аргументы инструмента должны быть словарём")
    key = _PARAMS[name]
    if set(arguments) != {key}:
        raise ValueError(f"Инструмент {name} принимает только параметр {key}")
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"У инструмента {name} пустой параметр {key}")
    cleaned = value.strip()
    limit = 64 if key == "client_id" else 500
    if len(cleaned) > limit:
        raise ValueError(f"Параметр {key} длиннее {limit} символов")
    return {key: cleaned}


def _call(name: str, arguments: dict) -> dict:
    if name == "get_profile":
        return _profile_result(arguments["client_id"])
    if name == "search_news":
        items = _search_news(arguments["query"])
        return _envelope(name, "news", items, "" if items else "Поиск не нашёл записей.")
    if name == "search_bank_analytics":
        found = bank_analytics(arguments["query"])
        items = [
            _evidence(hit, f"{hit['id']}:{hit['page']}", str(hit.get("source_type") or "bank_analytics"))
            for hit in found["hits"]
        ]
        return _envelope(name, "bank_analytics", items, str(found.get("message") or ""))
    return _counterparty_result(arguments["client_id"])


def _profile_result(client_id: str) -> dict:
    profile = get_profile(client_id)
    if profile is None:
        return _envelope("get_profile", "company_profile", [], "Компания не найдена.")
    item = _evidence(profile.model_dump(), profile.client_id, "company_profile")
    return _envelope("get_profile", "company_profile", [item], evidence_id=profile.client_id)


def _counterparty_result(client_id: str) -> dict:
    profile = get_profile(client_id)
    if profile is None:
        return _envelope("get_counterparties", "payments", [], "Компания не найдена.")
    items = [
        _evidence(item.model_dump(), f"{item.client_id}:{item.direction}", "payments")
        for item in profile.counterparties
    ]
    message = "" if items else "Контрагенты не найдены."
    return _envelope("get_counterparties", "payments", items, message, evidence_id=client_id if items else "")


def _envelope(tool: str, source: str, items: list[dict], message: str = "", evidence_id: str = "") -> dict:
    return {
        "tool": tool,
        "status": "ok" if items else "none",
        "evidence_id": evidence_id or (items[0]["evidence_id"] if items else ""),
        "source": source,
        "message": message,
        "items": items,
    }


def _evidence(payload: dict, evidence_id: str, source: str) -> dict:
    return {"evidence_id": evidence_id, "source": source, **payload}


def _news_source(hit: dict) -> str:
    name = str(hit.get("source_name") or hit.get("source") or "").strip()
    url = str(hit.get("url") or "").strip()
    return name or url or "news"


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
