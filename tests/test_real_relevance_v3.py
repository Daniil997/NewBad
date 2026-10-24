"""Relevance uses explainable profile evidence, not a model's unsupported confidence."""

from app.corpus.search import NewsItem
from app.personal_feed import (
    CLEAR_LINK, STRONG_LINK, WEAK_LINK, calibrate_model_result, evaluate,
)


def article(title="Изменились условия кредитования предприятий", event="monetary_policy"):
    return NewsItem(
        id="example-news", title=title, url="https://www.cbr.ru/press/event/?id=1",
        published_at="2026-10-24",
        excerpt="Банк России опубликовал решение об условиях кредитования предприятий.",
        event_type=event, event_title="Событие", status="imported",
        source="Банк России",
    )


def test_only_category_is_not_a_confirmed_company_link():
    result = evaluate({"company_name": "Совершенно другая организация"}, article())
    assert result["score"] < WEAK_LINK
    assert result["relevant"] is False


def test_industry_without_other_evidence_is_weak_not_strong():
    result = evaluate(
        {"industry_tags": ["manufacturing"]},
        article("Рынок металлов и продукции", "metals"),
    )
    assert WEAK_LINK <= result["score"] < CLEAR_LINK
    assert result["relevant"] is True


def test_two_independent_profile_signals_produce_strong_link():
    result = evaluate(
        {"focus_topics": ["rates_inflation"], "credit_interest": "interested"},
        article(),
    )
    assert STRONG_LINK <= result["score"] <= 1
    assert len(result["reasons"]) == 2


def test_company_mention_is_strong_but_single_term_is_not():
    named = evaluate({"company_name": "Северные перевозки"}, article("Новости компании Северные перевозки"))
    assert named["score"] >= STRONG_LINK
    one_term = evaluate(
        {"company_name": "Другая компания", "products_services": "перевозки контейнеров"},
        article("Изменились перевозки", "sanctions_trade"),
    )
    assert one_term["score"] < STRONG_LINK


def test_unsubstantiated_llm_score_cannot_create_strong_relevance():
    result = calibrate_model_result(
        {}, article(), {"score": 0.99, "relevant": True, "reasons": ["Очень важно"], "method": "LLM"},
    )
    assert result["score"] < WEAK_LINK
    assert result["relevant"] is False


def test_model_complements_explainable_baseline_without_replacing_it():
    result = calibrate_model_result(
        {"focus_topics": ["rates_inflation"]},
        article(),
        {"score": 0.95, "relevant": True, "reasons": ["Возможное влияние на ставку"]},
    )
    assert CLEAR_LINK <= result["score"] < STRONG_LINK
    assert any("Оценка модели" in reason for reason in result["reasons"])
    assert "Правила" in result["method"]


def test_country_russia_alone_is_not_a_business_specific_geography_signal():
    result = evaluate(
        {"country": "Россия"},
        article("Россия: опубликован обзор ключевой ставки"),
    )
    assert not result["relevant"]
    assert result["score"] < WEAK_LINK
