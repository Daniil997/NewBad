import json
import re

import yaml
from pydantic import BaseModel, model_validator
from rank_bm25 import BM25Okapi

from app.db import ROOT

_TOKEN = re.compile(r"[a-zа-яё0-9%]+", re.IGNORECASE)


class NewsItem(BaseModel):
    id: str
    url: str
    title: str
    published_at: str
    excerpt: str
    event_type: str | None
    event_title: str | None


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


def load_news() -> list[NewsItem]:
    taxonomy = _taxonomy()
    items: list[NewsItem] = []
    for path in sorted((ROOT / "data" / "news").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        event_type = classify(f"{raw['title']} {raw['excerpt']}", taxonomy)
        spec = taxonomy["event_types"].get(event_type or "", {})
        items.append(
            NewsItem(
                id=raw["id"],
                url=raw["url"],
                title=raw["title"],
                published_at=raw["published_at"],
                excerpt=raw["excerpt"],
                event_type=event_type,
                event_title=spec.get("title"),
            )
        )
    return items


def load_bank_documents() -> list[BankDocument]:
    documents: list[BankDocument] = []
    folder = ROOT / "data" / "bank"
    if not folder.is_dir():
        return documents
    for path in sorted(folder.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        documents.append(BankDocument.model_validate(raw))
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


def search_news(query: str, limit: int = 5) -> list[SearchHit]:
    news = load_news()
    ranked = _rank(query, [f"{item.title} {item.excerpt}" for item in news])
    hits: list[SearchHit] = []
    for index, score in ranked[:limit]:
        item = news[index]
        hits.append(
            SearchHit(
                id=item.id,
                title=item.title,
                score=round(score, 4),
                excerpt=item.excerpt,
                event_type=item.event_type,
            )
        )
    return hits


def search_bank_analytics(query: str, limit: int = 5) -> list[SearchHit]:
    documents = load_bank_documents()
    corpus = [f"{item.title} {' '.join(item.theses)} {item.text}" for item in documents]
    ranked = _rank(query, corpus)
    hits: list[SearchHit] = []
    for index, score in ranked[:limit]:
        item = documents[index]
        hits.append(
            SearchHit(
                id=item.doc_id,
                title=item.title,
                score=round(score, 4),
                excerpt=item.quote,
                event_type=item.topic,
            )
        )
    return hits


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


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(_fold(text))


def _fold(text: str) -> str:
    return text.lower().replace("ё", "е")
