"""Business regression cases: a related category is not sufficient evidence for a bank claim."""

import json

from app.agent import generate
from app.bank_store import bank_evidence, ensure_recommendations, selected_evidence
from app.corpus.search import NewsItem, load_news
from app.llm.router import Route
from app.news_store import bank_recommendations, store_imported_news
from app.news_topics import news_topic
from app.personal_feed import evaluate
from app.user_store import business_profile_for_owner, create_user, save_business_profile

FUEL = {"industry": "Торговля оптовая моторным топливом, включая авиационный бензин",
        "okved": "46.71.2", "okved_theme": "energy", "product_credit": "yes", "customs_activity": "yes"}
PROPERTY = {"industry": "Аренда нежилой недвижимости", "okved": "68.20.2",
            "okved_theme": "real_estate", "product_deposit": "yes", "product_credit": "no"}
WHOLESALE = {"industry": "Торговля оптовая неспециализированная", "okved": "46.90", "okved_theme": "trade",
             "product_credit": "no", "customs_activity": "no"}


def news(title, body="", identifier="example"):
    event, label = news_topic(title, body)
    return NewsItem(id=identifier, title=title, excerpt=body, event_type=event, event_title=label,
                    url=f"https://example.org/{identifier}", published_at="2026-10-25")


def test_word_boundaries_do_not_turn_shops_or_roundtables_into_gas_news():
    for title in ("Российские ритейлеры стали чаще закрывать магазины", "Евразийский круглый стол",
                  "Страхование ответственности владельцев опасных объектов"):
        assert news_topic(title, "")[0] != "commodities_energy"
    assert news_topic("Поставки дизеля и бензина", "")[0] == "commodities_energy"


def test_announcements_and_household_mortgages_do_not_get_corporate_rate_assessments():
    for title in ("Приглашаем исследователей на конференцию по денежно-кредитной политике",
                  "23 октября состоится пресс-конференция по ключевой ставке",
                  "ЦБ уточнил расчеты ставки по семейной ипотеке"):
        item = news(title)
        for profile in (FUEL, PROPERTY, WHOLESALE):
            assert not evaluate(profile, item)["relevant"]
            assert bank_evidence(profile, item)[0] == []


def test_okved_works_without_ui_tags_and_does_not_invent_fuel_exposure_for_wholesalers():
    diesel = news("Новак заявил о готовности обеспечить потребности партнеров в дизеле")
    tax = news("Владельцы ПВЗ попросили не облагать налогом кадастровую стоимость помещений")
    retail = news("Российские ритейлеры стали чаще закрывать физические магазины")
    assert evaluate(FUEL, diesel)["relevant"]
    assert evaluate(PROPERTY, tax)["relevant"]
    assert evaluate(WHOLESALE, retail)["relevant"]
    assert not evaluate(FUEL, retail)["relevant"]
    assert not evaluate(WHOLESALE, diesel)["relevant"]
    assert not evaluate(PROPERTY, diesel)["relevant"]
    assert "industry_tags" not in FUEL


def test_bank_source_must_explain_the_specific_event_and_respect_time():
    diesel = news("Новак заявил о готовности обеспечить потребности партнеров в дизеле")
    sources, _ = bank_evidence(FUEL, diesel)
    assert sources
    assert all("топлив" in source["text"] or "бензин" in source["text"] for source in sources)
    assert all(source["document_id"] != "macro_7m" for source in sources)
    rates = news("Банк России сохранил ключевую ставку", "Условия кредитования остаются жесткими.")
    sources, _ = bank_evidence(FUEL, rates)
    assert sources[0]["document_id"] == "key_rate"
    assert not any(source["passage_id"] == "key_rate:reviewed:1" for source in sources)
    fx = news("Курсы валют: курс рубля к доллару", "Доллар подорожал.")
    assert not any(source["document_id"] == "gold" for source in bank_evidence(FUEL, fx)[0])


def test_retail_demand_is_distinct_from_industrial_consumption_and_freight():
    from app.relevance import concepts

    assert "warehousing" not in concepts("Складывающаяся ситуация на рынке топлива")
    steel = news("Северсталь улучшила прогноз потребления стали", "Растет спрос со стороны строительства.")
    freight = news("Спрос на автоперевозки грузов растет", "Локальный дефицит предложения на фоне топливного кризиса.")
    gas = news("ЕС нарастил импорт газа по трубопроводам")
    for item in (steel, freight, gas):
        assert bank_evidence(PROPERTY, item)[0] == []
    assert bank_evidence(FUEL, gas)[0] == []
    retail = news("Российские ритейлеры стали чаще закрывать физические магазины")
    sources, _ = bank_evidence(PROPERTY, retail)
    assert sources and sources[0]["document_id"] == "consumption"
    premium = news("Премиальные клиенты банков привыкают к индивидуальным программам", "Спрос на банковские услуги вырос.")
    assert bank_evidence(PROPERTY, premium)[0] == []


def test_model_can_decline_candidates_and_decision_is_reused_by_report_and_chat():
    user = create_user("model_reader", "test-hash")
    save_business_profile(user["id"], {"company_name": "Проверка", **FUEL}, "complete")
    saved = business_profile_for_owner(user["id"])
    item = news("Банк России сохранил ключевую ставку", "Условия кредитования остаются жесткими.")
    store_imported_news("test", [{"id": item.id, "title": item.title, "text": item.excerpt, "url": item.url,
                                  "published_at": item.published_at, "loaded_at": item.published_at,
                                  "source": "Тест", "sha256": "a" * 64}])
    item = load_news()[0]
    ensure_recommendations(saved["profile_id"], [item])
    assert bank_recommendations(saved["profile_id"])
    ensure_recommendations(saved["profile_id"], [item], {item.id: {
        "score": 0.7, "relevant": True, "reasons": ["Стоимость кредитования"], "bank_source_ids": [],
        "bank_selection_reason": "Предоставленные выдержки не раскрывают изменение условий этого договора.",
    }})
    assert bank_recommendations(saved["profile_id"]) == {}
    assert selected_evidence(saved, item) == []
    ensure_recommendations(saved["profile_id"], [item])
    assert selected_evidence(saved, item) == []


def test_model_cannot_select_unprovided_source_ids(monkeypatch):
    route = Route(mode="local_only", provider="local", model="test")
    item = news("Банк России сохранил ключевую ставку", "Условия кредитования остаются жесткими.")
    def reply(*_args, **_kwargs):
        return {"items": [{"id": item.id, "score": 0.7, "relevant": True, "reason": "Изменение стоимости кредитования",
                           "bank_source_ids": ["invented-source"], "bank_selection_reason": "Подтверждает стоимость кредита"}]}, route, 1
    monkeypatch.setattr(generate, "_ask", reply)
    result = generate.select_profile_news(FUEL, [item])
    assert "bank_source_ids" not in result[item.id]
    assert "invented-source" not in json.dumps(result)


def test_source_specific_model_check_is_used_when_ranking_response_omits_it(monkeypatch):
    route = Route(mode="local_only", provider="local", model="test")
    item = news("Банк России сохранил ключевую ставку", "Условия кредитования остаются жесткими.")
    allowed = bank_evidence(FUEL, item, limit=5, allow_weak_profile=True)[0]
    calls = []
    def reply(messages, *_args, **_kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return {"items": [{"id": item.id, "score": 0.7, "relevant": True, "reason": "Стоимость кредитов компании"}]}, route, 1
        return {"decisions": [{"news_id": item.id, "source_ids": [allowed[0]["passage_id"]],
                                "reason": "Позиция по ключевой ставке объясняет стоимость кредитования"}]}, route, 1
    monkeypatch.setattr(generate, "_ask", reply)
    result = generate.select_profile_news(FUEL, [item])
    assert len(calls) == 2
    assert result[item.id]["bank_source_ids"] == [allowed[0]["passage_id"]]


def test_connection_retry_does_not_repeat_inference_after_read_timeout():
    import httpx
    import pytest
    from app.llm.client import post_chat

    calls = []
    def transport(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("transient", request=request)
        return httpx.Response(200, json={"ok": True})
    assert post_chat("https://example.org/chat", {}, 1, transport=httpx.MockTransport(transport)) == {"ok": True}
    assert len(calls) == 2
    calls.clear()
    def timed_out(request):
        calls.append(request)
        raise httpx.ReadTimeout("inference response not received", request=request)
    with pytest.raises(httpx.ReadTimeout):
        post_chat("https://example.org/chat", {}, 1, transport=httpx.MockTransport(timed_out))
    assert len(calls) == 1


def test_manual_retry_can_replace_rules_after_a_model_outage():
    from app.personal_feed import analysis_results, missing_candidates, save_job_results

    user = create_user("retry_reader", "test-hash")
    save_business_profile(user["id"], {"company_name": "Проверка", **FUEL}, "complete")
    saved = business_profile_for_owner(user["id"])
    item = news("Банк России сохранил ключевую ставку", "Условия кредитования остаются жесткими.")
    job = {"id": "audit-test", "profile_id": saved["profile_id"], "profile_version": saved["profile_version"], "candidate_ids": [item.id]}
    save_job_results(job, saved["profile"], {item.id: item})
    assert missing_candidates(saved["profile_id"], saved["profile_version"], [item]) == []
    assert missing_candidates(saved["profile_id"], saved["profile_version"], [item], retry_rules=True) == [item.id]
    sources = bank_evidence(saved["profile"], item, limit=5)[0]
    save_job_results(job, saved["profile"], {item.id: item}, {item.id: {
        "relevant": True, "score": 0.8, "reasons": ["Стоимость заемного капитала"],
        "bank_source_ids": [sources[0]["passage_id"]], "bank_selection_reason": "Оценка ключевой ставки относится к кредитным продуктам компании",
    }})
    assert missing_candidates(saved["profile_id"], saved["profile_version"], [item], retry_rules=True) == []
    assert "модели" in analysis_results(saved["profile_id"], saved["profile_version"])["items"][item.id]["method"]
