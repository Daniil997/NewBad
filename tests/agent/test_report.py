import json

from app.agent.generate import _is_placeholder, analyze_profile_news
from app.corpus.search import NewsItem
from app.llm.router import Route, TierResult

_ROUTE = Route(mode="local_only", provider="local", model="gemma-4-26b-a4b")
_NEWS = NewsItem(
    id="news-001", url="https://example.org/news-001", title="Регулятор изменил условия страхования",
    published_at="2026-10-23", excerpt="Расширен тарифный коридор страхования ответственности.",
    event_type="regulation", event_title="Регулирование", source="Официальный источник", status="imported",
)
_PROFILE = {
    "company_name": "ООО Север", "industry": "Логистика", "customer_types": "производители упаковки",
    "dependencies": "топливо и страхование перевозок", "market_geographies": ["Казахстан"],
    "credit_interest": "interested",
}
_PLACEHOLDER_REPLY = {
    "summary": "Регулятор расширил тарифный коридор страхования ответственности.",
    "relevance": "Новость может косвенно затронуть расходы перевозчика на страхование грузов.",
    "risks": "Данные о рисках отсутствуют.",
    "opportunities": "Данные о возможностях отсутствуют.",
    "questions": ["Как изменится стоимость страхования?"],
    "evidence_ids": ["company-profile", "news:news-001"],
}
_GOOD_REPLY = {
    "summary": "Регулятор расширил тарифный коридор страхования ответственности.",
    "relevance": "Оценка: для перевозчика это косвенный фактор, он влияет на стоимость страхования грузов.",
    "risks": "Оценка: страховые взносы по перевозкам могут вырасти, и это увеличит себестоимость услуг компании.",
    "opportunities": "Оценка: можно заранее пересогласовать условия страхования с партнёрами и закрепить цену на более выгодных условиях.",
    "questions": ["Какая доля затрат приходится на страхование перевозок?"],
    "evidence_ids": ["company-profile", "news:news-001"],
}


def _install(monkeypatch, replies, route: Route = _ROUTE):
    calls: list[list[dict]] = []

    def fake(messages, *, tier, max_tokens, **_options):
        del tier, max_tokens
        calls.append(list(messages))
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return TierResult(route=route, text=text, tokens=1)

    monkeypatch.setattr("app.agent.generate.complete_tier", fake)
    monkeypatch.setattr("app.agent.generate.developer_settings", lambda: {"validate_numeric_grounding": True})
    return calls


def test_placeholders_are_detected_but_real_conclusions_are_not():
    assert _is_placeholder("Данные о рисках отсутствуют.")
    assert _is_placeholder("Данные о возможностях отсутствуют.")
    assert _is_placeholder("Нет данных.")
    assert _is_placeholder("Недостаточно информации для вывода о влиянии на компанию.")
    assert not _is_placeholder(_GOOD_REPLY["risks"])
    assert not _is_placeholder(
        "Прямых данных о компании в публикации нет, но оценка такая: рост страховых взносов "
        "по перевозкам может увеличить себестоимость услуг и снизить маржу."
    )


def test_report_retries_when_the_model_answers_with_placeholders(monkeypatch):
    calls = _install(monkeypatch, [_PLACEHOLDER_REPLY, _GOOD_REPLY])

    report = analyze_profile_news(_PROFILE, _NEWS)

    assert report is not None
    assert report["origin"] == "model"
    assert report["risks"] == _GOOD_REPLY["risks"]
    assert len(calls) == 2
    assert any("Разбор не принят" in message["content"] for message in calls[1])


def test_report_falls_back_to_profile_based_conclusions_instead_of_placeholders(monkeypatch):
    calls = _install(monkeypatch, [_PLACEHOLDER_REPLY])

    report = analyze_profile_news(_PROFILE, _NEWS)

    assert report is not None
    assert report["origin"] == "rules"
    assert len(calls) == 3
    for field in ("risks", "opportunities", "relevance"):
        assert not _is_placeholder(report[field])
        assert report[field].startswith("Оценка")
    assert "ООО Север" in report["risks"]
    assert report["evidence_ids"] == ["company-profile", "news:news-001"]
    assert report["questions"]


def test_fallback_for_a_sparse_profile_still_gives_conclusions(monkeypatch):
    _install(monkeypatch, [_PLACEHOLDER_REPLY])

    report = analyze_profile_news({"company_name": "ООО Пример"}, _NEWS)

    assert report is not None and report["origin"] == "rules"
    assert not _is_placeholder(report["risks"]) and not _is_placeholder(report["opportunities"])
    assert "если" in report["relevance"].lower()
    assert "мало" not in report["relevance"].lower()
    assert "заполн" not in report["relevance"].lower()


def test_report_adds_the_news_reference_and_keeps_grounding_for_numbers(monkeypatch):
    reply = {**_GOOD_REPLY, "evidence_ids": ["company-profile"]}
    _install(monkeypatch, [reply])
    report = analyze_profile_news(_PROFILE, _NEWS)
    assert report is not None and report["origin"] == "model"
    assert "news:news-001" in report["evidence_ids"]

    invented = {**_GOOD_REPLY, "risks": "Оценка: расходы на страхование вырастут на 40 процентов из-за новых тарифов."}
    _install(monkeypatch, [invented])
    fallback = analyze_profile_news(_PROFILE, _NEWS)
    assert fallback is not None and fallback["origin"] == "rules"
    assert "40" not in fallback["risks"]


def test_unavailable_model_is_not_hidden_behind_a_template(monkeypatch):
    mock_route = Route(mode="mock", provider="mock", model="mock-model", reason="режим mock")

    def fake(messages, *, tier, max_tokens, **_options):
        del messages, tier, max_tokens
        return TierResult(route=mock_route, text=None, tokens=0)

    monkeypatch.setattr("app.agent.generate.complete_tier", fake)
    monkeypatch.setattr("app.agent.generate.developer_settings", lambda: {"validate_numeric_grounding": True})

    assert analyze_profile_news(_PROFILE, _NEWS) is None
