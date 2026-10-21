import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ValidationError, model_validator
from rank_bm25 import BM25Okapi

from app.db import ROOT
from app.ingest.bank_documents import IngestedDocument

_TOKEN = re.compile(r"[a-zа-яё0-9%]+", re.IGNORECASE)
_ENDING = (
    "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими",
    "ее", "ие", "ые", "ое", "ая", "яя", "ой", "ый", "ий", "ых", "их",
    "ах", "ях", "ам", "ям", "ов", "ев", "ом", "ем", "ую", "юю",
    "а", "я", "ы", "и", "е", "о", "у", "ю",
)
_STOP = frozenset(
    "и в во на по с со к ко о об для что это как не ни от за из до при без над под про "
    "а но или же бы то так уже еще также только если когда где кто чем все весь она они "
    "он его ее этот эта эти быть мы вы их ли".split()
)
_COMMON_WORDS = (
    "банк данные рост цена год неделя уровень рынок прогноз компания анализ динамика информация материал отчёт"
)
_INDEXABLE = frozenset({"embedded", "ocr"})
_MIN_CHARS = 40
_NEWS_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STALE_DAYS = 180


class NewsItem(BaseModel):
    id: str
    url: str
    title: str
    published_at: str
    excerpt: str
    event_type: str | None
    event_title: str | None
    source: str | None = None
    loaded_at: str | None = None
    status: str = "demo"
    stale: bool = False


class BankDocument(BaseModel):
    doc_id: str
    title: str
    published_at: str
    topic: str
    page: int
    source_type: str
    theses: list[str]
    text: str
    quote: str

    @model_validator(mode="after")
    def quote_is_substring(self) -> "BankDocument":
        if self.quote not in self.text:
            raise ValueError(f"Цитата не найдена в тексте документа {self.doc_id}")
        if len(self.quote.split()) > 25:
            raise ValueError(f"Цитата документа {self.doc_id} длиннее 25 слов")
        return self


class SearchHit(BaseModel):
    id: str
    title: str
    score: float
    excerpt: str
    event_type: str | None = None
    published_at: str | None = None
    url: str | None = None
    page: int | None = None
    source_type: str | None = None
    strength: str | None = None
    stale: bool = False


class CorpusError(Exception):
    """load — файл корпуса не читается, index — индекс не построен. Пустой ответ ошибкой не является."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


def load_news(folder: Path | None = None, today: date | None = None) -> list[NewsItem]:
    """Демо-файлы и `imported/*.json` читаются в один формат. Дубли и неполные записи в корпус не входят."""
    root = folder or (ROOT / "data" / "news")
    if not root.is_dir():
        return []
    paths = sorted(path for path in root.glob("*.json") if path.name != "import.json")
    imported = root / "imported"
    if imported.is_dir():
        paths.extend(path for path in sorted(imported.glob("*.json")) if path.name != "import.json")
    taxonomy = _taxonomy()
    current = today or date.today()
    items: list[NewsItem] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for path in paths:
        record = _news_record(_read_news(path), current, taxonomy)
        if record is None:
            continue
        url_key = _article_key(record.url)
        if record.id in seen_ids or (url_key is not None and url_key in seen_urls):
            continue
        seen_ids.add(record.id)
        if url_key is not None:
            seen_urls.add(url_key)
        items.append(record)
    return items


def load_bank_documents(folder: Path | None = None) -> list[BankDocument]:
    """Паспорта из `data/bank/*.json` и страницы из `extracted/*/pages.json`. Пустые и сбойные страницы не входят."""
    root = folder or (ROOT / "data" / "bank")
    if not root.is_dir():
        return []
    documents = [_passport(path) for path in sorted(root.glob("*.json"))]
    extracted = root / "extracted"
    if extracted.is_dir():
        for path in sorted(extracted.glob("*/pages.json")):
            documents.extend(_page_fragments(path))
    return documents


def classify(text: str, taxonomy: dict | None = None) -> str | None:
    rules = (taxonomy or _taxonomy())["event_types"]
    haystack = _fold(text)
    best_id: str | None = None
    best_score = 0.0
    for event_type, spec in rules.items():
        hits = sum(1 for trigger in spec.get("triggers") or [] if _fold(trigger) in haystack)
        if hits == 0:
            continue
        score = hits * 10 + float(spec.get("weight") or 0)
        if score > best_score:
            best_id = event_type
            best_score = score
    return best_id


def search_news(
    query: str,
    limit: int = 5,
    folder: Path | None = None,
    today: date | None = None,
) -> list[SearchHit]:
    """Ищет по заголовку и тексту. Устаревшая новость остаётся в выдаче, но после свежих."""
    news = load_news(folder, today)
    ranked = _rank(query, [f"{item.title} {item.excerpt}" for item in news])
    hits: list[SearchHit] = []
    for index, score in ranked:
        item = news[index]
        hits.append(
            SearchHit(
                id=item.id,
                title=item.title,
                score=round(score, 4),
                excerpt=item.excerpt,
                event_type=item.event_type,
                published_at=item.published_at,
                url=item.url,
                stale=item.stale,
            )
        )
    hits.sort(key=lambda hit: (not hit.stale, hit.score), reverse=True)
    return hits[:limit]


def search_bank_analytics(query: str, limit: int = 5, folder: Path | None = None) -> list[SearchHit]:
    """Пустой список — совпадений нет. Сломанный файл или индекс поднимают CorpusError, а не пустой ответ."""
    documents = load_bank_documents(folder)
    content = [token for token in _tokenize(query, stem=True) if token not in _STOP]
    if not content or not documents:
        return []
    corpus = [f"{item.title} {' '.join(item.theses)} {item.text}" for item in documents]
    try:
        ranked = _rank_tokens(content, corpus)
    except Exception as error:
        raise CorpusError("index", f"Индекс банка не построен: {error.__class__.__name__}") from error
    hits: list[tuple[bool, SearchHit]] = []
    for index, score in ranked:
        item = documents[index]
        matched = set(content) & set(_tokenize(corpus[index], stem=True))
        strength = _strength(matched)
        if strength == "weak":
            continue
        hits.append(
            (
                bool(item.theses),
                SearchHit(
                    id=item.doc_id,
                    title=item.title,
                    score=round(score, 4),
                    excerpt=_excerpt(item, matched),
                    event_type=item.topic,
                    published_at=item.published_at,
                    page=item.page,
                    source_type=item.source_type,
                    strength=strength,
                ),
            )
        )
    hits.sort(key=_hit_order, reverse=True)
    return [hit for _curated, hit in hits[:limit]]


def _rank(query: str, documents: list[str]) -> list[tuple[int, float]]:
    tokens = _tokenize(query)
    if not tokens or not documents:
        return []
    corpus = [_tokenize(document) or ["_"] for document in documents]
    scores = BM25Okapi(corpus).get_scores(tokens)
    ranked: list[tuple[int, float]] = []
    for index, score in enumerate(scores):
        if not set(tokens) & set(corpus[index]):
            continue
        ranked.append((index, float(score)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _taxonomy() -> dict:
    return yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))


def _read_news(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CorpusError("load", f"Не читается новость {path.name}: {error.__class__.__name__}") from error
    if not isinstance(raw, dict):
        raise CorpusError("load", f"Новость {path.name} должна быть объектом")
    return raw


def _news_record(raw: dict, today: date, taxonomy: dict) -> NewsItem | None:
    if str(raw.get("status") or "") == "duplicate":
        return None
    title = str(raw.get("title") or "").strip()
    url = str(raw.get("url") or "").strip()
    published = str(raw.get("published_at") or "").strip()
    body = str(raw.get("excerpt") or raw.get("text") or "").strip()
    identifier = str(raw.get("id") or "").strip()
    if not identifier or not title or not body or not _news_url(url) or not _news_date(published):
        return None
    event_type = classify(f"{title} {body}", taxonomy)
    spec = taxonomy["event_types"].get(event_type or "", {})
    published_on = date.fromisoformat(published)
    return NewsItem(
        id=identifier,
        url=url,
        title=title,
        published_at=published,
        excerpt=body,
        event_type=event_type,
        event_title=spec.get("title"),
        source=str(raw["source"]).strip() if raw.get("source") else None,
        loaded_at=str(raw["loaded_at"]).strip() if raw.get("loaded_at") else None,
        status=str(raw.get("status") or "demo"),
        stale=(today - published_on).days > _STALE_DAYS,
    )


def _news_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _article_key(url: str) -> str | None:
    """Адрес главной страницы не считается идентификатором статьи: у демо-новостей он общий."""
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    if not parsed.query and not path:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{path}?{parsed.query}".lower()


def _news_date(value: str) -> bool:
    if _NEWS_DATE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _passport(path: Path) -> BankDocument:
    try:
        return BankDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise CorpusError("load", f"Не читается паспорт {path.name}: {error.__class__.__name__}") from error


def _page_fragments(path: Path) -> list[BankDocument]:
    try:
        document = IngestedDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise CorpusError("load", f"Не читаются страницы {path.parent.name}: {error.__class__.__name__}") from error
    if document.duplicate_of or not document.processed:
        return []
    fragments: list[BankDocument] = []
    for page in document.pages:
        text = _normalize(page.text)
        if page.error or page.kind not in _INDEXABLE or len(text) < _MIN_CHARS:
            continue
        quote = _quote(text)
        if quote is None:
            continue
        fragments.append(
            BankDocument(
                doc_id=page.doc_id,
                title=page.title,
                published_at=page.date,
                topic=page.topic,
                page=page.page,
                source_type=page.source,
                theses=[],
                text=text,
                quote=quote,
            )
        )
    return fragments


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _quote(text: str) -> str | None:
    words = text.split()
    if not words:
        return None
    return " ".join(words[:25])


def _excerpt(document: BankDocument, stems: set[str]) -> str:
    if stems & set(_tokenize(document.quote, stem=True)):
        return document.quote
    words = document.text.split()
    anchor = 0
    for position, word in enumerate(words):
        if _stem(_fold(word)) in stems:
            anchor = position
            break
    fragment = " ".join(words[max(0, anchor - 4):anchor + 21])
    if fragment and fragment in document.text:
        return fragment
    return document.quote


def _strength(matched: set[str]) -> str:
    specific = {token for token in matched if token not in _COMMON}
    if len(specific) >= 2 or (specific and len(matched) >= 2):
        return "strong"
    if specific:
        return "narrow"
    return "weak"


_STRENGTH_RANK = {"strong": 2, "narrow": 1}


def _hit_order(item: tuple[bool, SearchHit]) -> tuple[int, bool, float]:
    curated, hit = item
    return (_STRENGTH_RANK[hit.strength or "narrow"], curated, hit.score)


def _rank_tokens(tokens: list[str], documents: list[str]) -> list[tuple[int, float]]:
    corpus = [_tokenize(document, stem=True) or ["_"] for document in documents]
    scores = BM25Okapi(corpus).get_scores(tokens)
    ranked: list[tuple[int, float]] = []
    for index, score in enumerate(scores):
        if not set(tokens) & set(corpus[index]):
            continue
        ranked.append((index, float(score)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _tokenize(text: str, stem: bool = False) -> list[str]:
    tokens = _TOKEN.findall(_fold(text))
    if not stem:
        return tokens
    return [_stem(token) for token in tokens]


def _stem(token: str) -> str:
    if len(token) < 4 or any(character.isdigit() for character in token):
        return token
    for ending in _ENDING:
        if token.endswith(ending) and len(token) - len(ending) >= 3:
            return token[: -len(ending)]
    return token


def _fold(text: str) -> str:
    return text.lower().replace("ё", "е")


_COMMON = frozenset(_stem(_fold(word)) for word in _COMMON_WORDS.split())
