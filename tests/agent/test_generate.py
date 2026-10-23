import json

from app.agent.draft import draft_card
from app.agent.generate import answer_profile_chat, answer_with_model, evidence_packet, extract_fact, request_copy
from app.agent.loop_context import AgentContext
from app.agent.tools import find_bank, find_news
from app.llm.router import PlanChoice, Route, TierResult
from app.profile.build import get_profile
from app.user_store import init_app_db

_ROUTE = Route(mode="local_only", provider="local", model="gemma-4-26b-a4b")
_QUOTE = "сохранение ключевой ставки на уровне 14% означает, что стоимость заёмных денег пока не снижается"


def _context() -> AgentContext:
    profile = get_profile("U-BORROWER")
    news = find_news("cbr-rate-14")
    bank = find_bank("key_rate", 1)
    assert profile is not None and news is not None and bank is not None
    return AgentContext(
        client_id="U-BORROWER",
        event_id=news.id,
        profile=profile,
        news=news,
        bank=bank,
        matched=["rate_borrower"],
    )


def _install(monkeypatch, reply, route: Route | None = None):
    chosen = route or _ROUTE
    sent: list[dict] = []

    def fake(messages, *, tier, max_tokens, **_route_options):
        del tier, max_tokens
        sent.append(json.loads(messages[1]["content"]))
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return TierResult(route=chosen, text=text, tokens=1)

    monkeypatch.setattr("app.agent.generate.complete_tier", fake)
    return sent


def _good_copy() -> dict:
    return {
        "fact": {
            "text": "Ключевая ставка сохранена на уровне 14%.",
            "layer": "confirmed_fact",
            "evidence_ids": ["cbr-rate-14"],
        },
        "bank": {
            "text": _QUOTE,
            "layer": "bank_position",
            "evidence_ids": ["key_rate:1"],
        },
        "inference": {
            "layer": "model_inference",
            "evidence_ids": ["cbr-rate-14", "key_rate:1"],
            "why": "Ставка 14% удерживает стоимость кредита.",
            "risk": "Платёж по кредиту не снижается.",
            "opportunity": "График можно сверить до нового решения.",
            "next_step": "Сверить платёж со ставкой 14%.",
            "question": "Какая часть кредитов зависит от ставки?",
        },
    }


def test_model_receives_structured_evidence_with_separate_layers(monkeypatch):
    sent = _install(monkeypatch, _good_copy())
    ctx = _context()
    copy = request_copy(ctx)
    packet = evidence_packet(ctx)
    assert sent
    assert sent[0]["evidence"] == packet
    layers = {item["layer"] for item in packet}
    assert layers == {"confirmed_fact", "bank_position"}
    news = next(item for item in packet if item["evidence_id"] == "cbr-rate-14")
    bank = next(item for item in packet if item["evidence_id"] == "key_rate:1")
    feature = next(item for item in packet if item["source"] == "company_profile")
    assert news["layer"] == "confirmed_fact"
    assert news["source"] == "https://www.cbr.ru/press/"
    assert bank["layer"] == "bank_position"
    assert bank["source"] == "bank_analytics"
    assert feature["evidence_id"]
    assert copy is not None
    assert copy["fact"]["layer"] == "confirmed_fact"
    assert copy["bank"]["layer"] == "bank_position"
    assert copy["inference"]["layer"] == "model_inference"
    assert copy["why"].startswith("Ставка 14%")


def test_fact_without_a_known_evidence_id_is_dropped(monkeypatch):
    reply = _good_copy()
    reply["fact"]["evidence_ids"] = ["no-such-source"]
    _install(monkeypatch, reply)
    assert request_copy(_context()) is None


def test_model_cannot_invent_a_bank_source(monkeypatch):
    ctx = _context()
    ctx.bank = None
    reply = _good_copy()
    reply["inference"]["evidence_ids"] = ["cbr-rate-14"]
    _install(monkeypatch, reply)
    assert request_copy(ctx) is None


def test_invalid_json_does_not_escape(monkeypatch):
    _install(monkeypatch, "это не json {")
    assert request_copy(_context()) is None
    _install(monkeypatch, "просто текст без структуры")
    assert extract_fact(find_news("cbr-rate-14")) is None


def test_extract_fact_keeps_only_a_cited_news_fact(monkeypatch):
    news = find_news("cbr-rate-14")
    assert news is not None
    sent = _install(
        monkeypatch,
        {"text": "Ставка сохранена на уровне 14%.", "layer": "confirmed_fact", "evidence_ids": [news.id]},
    )
    assert extract_fact(news) == "Ставка сохранена на уровне 14%."
    assert sent[0]["evidence"][0]["evidence_id"] == news.id
    assert sent[0]["evidence"][0]["layer"] == "confirmed_fact"

    _install(monkeypatch, {"text": "Ставка 14%.", "layer": "confirmed_fact", "evidence_ids": ["другой-документ"]})
    assert extract_fact(news) is None

    _install(monkeypatch, {"text": "Ставка стала 15%.", "layer": "confirmed_fact", "evidence_ids": [news.id]})
    assert extract_fact(news) is None


def test_answer_rejects_an_unknown_source_and_broken_json(monkeypatch):
    evidence = [
        {
            "evidence_id": "cbr-rate-14",
            "source": "https://www.cbr.ru/press/",
            "layer": "confirmed_fact",
            "title": "Ставка",
            "text": "Ключевая ставка 14%.",
        }
    ]
    _install(monkeypatch, {"answer": "Ставка 14%.", "evidence_ids": ["cbr-rate-14"]})
    assert answer_with_model("какая ставка", "Ключевая ставка 14%.", evidence) == "Ставка 14%."
    _install(monkeypatch, {"answer": "Ставка 14%.", "evidence_ids": ["выдуманный-сайт"]})
    assert answer_with_model("какая ставка", "Ключевая ставка 14%.", evidence) is None
    _install(monkeypatch, "не объект")
    assert answer_with_model("какая ставка", "Ключевая ставка 14%.", evidence) is None


def test_profile_chat_ignores_numbered_list_markers_but_rejects_unsupported_numbers(monkeypatch):
    evidence = [
        {"evidence_id": "company-profile", "text": "Компания оказывает услуги связи."},
        {"evidence_id": "news:item", "text": "В материале описаны изменения тарифов."},
    ]
    _install(monkeypatch, {
        "answer": "1. В материале описаны изменения тарифов.\\n2. Оценка: это может повлиять на расходы компании.",
        "evidence_ids": ["company-profile", "news:item"],
    })
    result = answer_profile_chat("Как это повлияет?", [], evidence)
    assert result is not None

    _install(monkeypatch, {
        "answer": "1. В материале описаны изменения тарифов.\\n2. Расходы вырастут на 14 процентов.",
        "evidence_ids": ["company-profile", "news:item"],
    })
    assert answer_profile_chat("Как это повлияет?", [], evidence) is None


def test_mock_provider_does_not_call_the_model(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    init_app_db()

    def explode(*_args, **_kwargs):
        raise AssertionError("модель не должна вызываться")

    monkeypatch.setattr("app.llm.router.complete", explode)
    monkeypatch.setattr("app.llm.client.post_chat", explode)
    ctx = _context()
    assert request_copy(ctx) is None
    assert ctx.generation_origin == "template"
    assert extract_fact(find_news("cbr-rate-14")) is None


def test_cloud_copy_uses_the_heavy_route(monkeypatch):
    route = Route(
        mode="cloud_only",
        provider="cloud",
        model="google/gemma-4-26b-a4b-it",
        reason="тест",
    )
    seen: dict[str, object] = {}

    def fake(_messages, *, tier, max_tokens, **_route_options):
        seen["tier"] = tier
        seen["max_tokens"] = max_tokens
        return TierResult(route=route, text=json.dumps(_good_copy(), ensure_ascii=False), tokens=5)

    monkeypatch.setattr("app.agent.generate.complete_tier", fake)
    ctx = _context()
    ctx.plan = PlanChoice(
        tools=["get_profile"],
        model=route.model,
        provider="cloud",
        mode="cloud_only",
        tokens=1,
    )
    card = draft_card(ctx)
    assert seen == {"tier": "heavy", "max_tokens": 400}
    assert card.llm_trace.origin == "model"
    assert card.llm_trace.mode == "cloud_only"
    assert card.llm_trace.provider == "cloud"
    assert card.llm_trace.model == "google/gemma-4-26b-a4b-it"


def test_failed_model_text_stays_a_template(monkeypatch):
    _install(monkeypatch, "это не json")
    ctx = _context()
    ctx.plan = PlanChoice(
        tools=["get_profile"],
        model="gemma-4-26b-a4b",
        provider="local",
        mode="local_only",
        tokens=3,
    )
    card = draft_card(ctx)
    assert card.llm_trace.origin == "template"
    assert card.llm_trace.mode == "template"
    assert card.llm_trace.provider == "template"
    assert "gemma" not in card.llm_trace.model


def test_profile_chat_assesses_indirect_impact_without_inventing_numbers(monkeypatch):
    prompts: list[str] = []

    def fake(messages, *, tier, max_tokens, **_route_options):
        del tier, max_tokens
        prompts.append(messages[0]["content"])
        return TierResult(route=_ROUTE, text=json.dumps({
            "answer": (
                "В материале сказано о расширении тарифного коридора страхования ответственности. "
                "Оценка для компании: у оператора связи нет прямого отношения к опасным производствам, "
                "косвенно вопрос может затронуть страхование инфраструктуры и подрядчиков."
            ),
            "evidence_ids": ["company-profile", "news:item"],
        }, ensure_ascii=False), tokens=1)

    monkeypatch.setattr("app.agent.generate.complete_tier", fake)
    evidence = [
        {"evidence_id": "company-profile", "text": "Компания: ПАО «МТС». Описание: связь и цифровые сервисы."},
        {"evidence_id": "news:item", "text": "Расширение тарифного коридора страхования ответственности."},
    ]
    result = answer_profile_chat("Как косвенно это может повлиять?", [], evidence)
    assert result is not None
    assert "Оценка для компании" in result["answer"]
    assert result["evidence_ids"] == ["company-profile", "news:item"]
    assert "не отказывай" in prompts[0]
    assert "focus=true" in prompts[0]

    def invented(messages, *, tier, max_tokens, **_route_options):
        del messages, tier, max_tokens
        return TierResult(route=_ROUTE, text=json.dumps({
            "answer": "Оценка: расходы вырастут на 14 процентов.",
            "evidence_ids": ["news:item"],
        }, ensure_ascii=False), tokens=1)

    monkeypatch.setattr("app.agent.generate.complete_tier", invented)
    assert answer_profile_chat("Как это повлияет?", [], evidence) is None
