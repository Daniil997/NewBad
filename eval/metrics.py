"""Измерения демо-прогона: ссылки, лента, документы банка, стоимость.

Цена токена читается из конфигурации. Если тарифа нет, ставка остаётся 0.
"""

import json
import math
import re
from datetime import date

import yaml

from app.agent.pasted import load_pasted_news
from app.corpus.search import CorpusError, _page_fragments, load_bank_documents, load_news
from app.db import ROOT
from app.llm.router import budget_policy
from app.profile.build import get_profile
from app.schemas.card import Card
from app.scoring.feed import _score_feed

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def cases() -> dict:
    return yaml.safe_load((ROOT / "eval" / "personalization_cases.yaml").read_text(encoding="utf-8"))


def factual_link_coverage(card: Card) -> float:
    """Доля фактов и позиций банка, у которых есть ссылка на источник карточки."""
    facts = [claim for claim in card.all_claims() if claim.layer in {"confirmed_fact", "bank_position"}]
    if not facts:
        return 0.0
    known = {source.id for source in card.sources}
    linked = sum(1 for claim in facts if claim.source_refs and set(claim.source_refs) <= known)
    return linked / len(facts)


def source_supported_share(card: Card) -> float:
    """Доля утверждений, которые опираются на текст источника или признак профиля."""
    claims = card.all_claims()
    if not claims:
        return 0.0
    news = _news_index()
    bank = load_bank_documents()
    supported = sum(1 for claim in claims if _supported(card, claim, news, bank))
    return supported / len(claims)


def unsupported_claim_ids(card: Card) -> list[str]:
    news = _news_index()
    bank = load_bank_documents()
    return [claim.id for claim in card.all_claims() if not _supported(card, claim, news, bank)]


def _news_index() -> dict:
    return {item.id: item for item in [*load_news(), *load_pasted_news()]}


def feed_quality(client_id: str, today: date | None = None) -> dict:
    """Точность топа, nDCG и дубли. Новость без оценки в разметке считается нулём."""
    payload = cases()
    day = today or date.fromisoformat(str(payload["today"]))
    profile = get_profile(client_id)
    if profile is None:
        raise ValueError(f"Компания не найдена: {client_id}")
    feed = _score_feed(profile, day, "eval")
    grades = {row["news"]: int(row["grade"]) for row in payload["grades"] if row["company"] == client_id}
    ordered = [*feed.top, *feed.less_relevant]
    ids = [item.news_id for item in ordered]
    ranks = {news_id: index for index, news_id in enumerate(ids)}
    judged = [grades.get(news_id, 0) for news_id in ids]
    head = judged[:3]
    inversions = 0
    for high, high_grade in grades.items():
        if high_grade < 2 or high not in ranks:
            continue
        for low, low_grade in grades.items():
            if low_grade != 0 or low not in ranks:
                continue
            if ranks[high] > ranks[low]:
                inversions += 1
    return {
        "client_id": client_id,
        "today": day.isoformat(),
        "top_news_id": ids[0] if ids else "",
        "duplicate_ids": len(ids) - len(set(ids)),
        "duplicate_penalty": sum(1 for item in ordered if any(factor.id == "duplicate" for factor in item.factors)),
        "duplicate_below_fresh": _duplicate_below_fresh(ordered),
        "missing_labels": [news_id for news_id in grades if news_id not in ranks],
        "precision_at_3": (sum(grade >= 1 for grade in head) / len(head)) if head else 0.0,
        "ndcg_at_5": _ndcg(judged, 5),
        "grade_inversions": inversions,
    }


def bank_processing_coverage() -> dict:
    """Сколько документов каталога прочитано и сколько страниц попало в индекс."""
    catalog = yaml.safe_load((ROOT / "config" / "bank_appendix.yaml").read_text(encoding="utf-8"))["documents"]
    root = ROOT / "data" / "bank" / "extracted"
    loaded = {(item.doc_id, item.page, item.text) for item in load_bank_documents()}
    pages_total = 0
    pages_indexed = 0
    pages_failed = 0
    documents = []
    for row in catalog:
        doc_id = str(row["id"])
        path = root / doc_id / "pages.json"
        record = {"id": doc_id, "processed": False, "pages": 0, "indexed": 0, "failed": 0, "reason": ""}
        if not path.is_file():
            record["reason"] = "нет pages.json"
            documents.append(record)
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fragments = _page_fragments(path)
        except (OSError, json.JSONDecodeError, CorpusError) as error:
            record["reason"] = error.__class__.__name__
            documents.append(record)
            continue
        pages = payload.get("pages") if isinstance(payload, dict) else None
        pages = pages if isinstance(pages, list) else []
        record["pages"] = len(pages)
        record["processed"] = bool(payload.get("processed")) and not payload.get("duplicate_of")
        record["failed"] = sum(1 for page in pages if isinstance(page, dict) and page.get("error"))
        indexed = sum(1 for item in fragments if (item.doc_id, item.page, item.text) in loaded)
        record["indexed"] = indexed
        if record["processed"] and indexed == 0 and pages:
            record["reason"] = "страницы не вошли в индекс"
        pages_total += len(pages)
        pages_indexed += indexed
        pages_failed += record["failed"]
        documents.append(record)
    processed = sum(1 for item in documents if item["processed"])
    catalog_count = len(catalog)
    return {
        "catalog": catalog_count,
        "processed_documents": processed,
        "document_coverage": processed / catalog_count if catalog_count else 0.0,
        "pages_total": pages_total,
        "pages_indexed": pages_indexed,
        "pages_failed": pages_failed,
        "pages_skipped": pages_total - pages_indexed,
        "page_coverage": pages_indexed / pages_total if pages_total else 0.0,
        "passports": len(list((ROOT / "data" / "bank").glob("*.json"))),
        "documents": documents,
    }


def token_cost(tokens: int, rate: float) -> float:
    """Стоимость пакета: число токенов на ставку из конфигурации."""
    return tokens / 1000 * rate


def request_usage(card: Card) -> dict[str, float | int]:
    """Задержка и токены карточки. Стоимость считается через token_cost."""
    rate = float(budget_policy()["rub_per_1k_tokens"])
    tokens = card.llm_trace.tokens
    return {
        "latency_ms": card.llm_trace.latency_ms,
        "tokens": tokens,
        "rub_per_1k_tokens": rate,
        "cost_rub": token_cost(tokens, rate),
    }


def _supported(card: Card, claim, news: dict, bank: list) -> bool:
    known = {source.id for source in card.sources}
    if claim.layer == "confirmed_fact":
        return bool(claim.source_refs) and all(_fact_in(claim.text, ref, news) for ref in claim.source_refs)
    if claim.layer == "bank_position":
        return bool(claim.source_refs) and all(_bank_has(claim.text, ref, card, bank) for ref in claim.source_refs)
    refs = [*claim.source_refs, *claim.feature_refs]
    profile_ids = {item.feature_id for item in card.profile_facts}
    if not refs or any(ref not in known and ref not in profile_ids for ref in refs):
        return False
    numbers = _numbers(claim.text)
    if not numbers:
        return True
    return numbers <= _numbers(_inference_corpus(card, claim, news, bank))


def _fact_in(text: str, source_id: str, news: dict) -> bool:
    item = news.get(source_id)
    if item is None:
        return False
    return text in item.excerpt or text in f"{item.title}. {item.excerpt}"


def _bank_has(text: str, doc_id: str, card: Card, bank: list) -> bool:
    pages = [item for item in bank if item.doc_id == doc_id]
    if card.bank_view.doc_ref == doc_id and card.bank_view.page is not None:
        same = [item for item in pages if item.page == card.bank_view.page]
        pages = same or pages
    return any(text in item.text for item in pages)


def _inference_corpus(card: Card, claim, news: dict, bank: list) -> str:
    parts = []
    for ref in claim.source_refs:
        item = news.get(ref)
        if item is not None:
            parts.extend([item.title, item.excerpt])
        parts.extend(page.text for page in bank if page.doc_id == ref)
    values = {item.feature_id: item.value for item in card.profile_facts}
    parts.extend(values.get(ref, "") for ref in claim.feature_refs)
    return " ".join(parts)


def _duplicate_below_fresh(items) -> bool:
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(item.event_type or "", []).append(item)
    for item in items:
        if not any(factor.id == "duplicate" for factor in item.factors):
            continue
        others = [other.score for other in grouped[item.event_type or ""] if other.news_id != item.news_id]
        if not others or item.score >= max(others):
            return False
    return True


def _ndcg(grades: list[int], limit: int) -> float:
    ideal = _dcg(sorted(grades, reverse=True), limit)
    if ideal == 0:
        return 0.0
    return _dcg(grades, limit) / ideal


def _dcg(grades: list[int], limit: int) -> float:
    total = 0.0
    for index, grade in enumerate(grades[:limit], start=1):
        total += (2**grade - 1) / math.log2(index + 1)
    return total


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", ".") for match in _NUMBER.finditer(text)}
