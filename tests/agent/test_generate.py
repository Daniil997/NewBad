import json

from app.agent.generate import answer_with_model, evidence_packet, extract_fact, request_copy
from app.agent.loop_context import AgentContext
from app.agent.tools import find_bank, find_news
from app.llm.client import Completion
from app.llm.router import Route
from app.profile.build import get_profile

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


def _install(monkeypatch, reply):
    sent: list[dict] = []

    def complete(messages, **_kwargs):
        sent.append(json.loads(messages[1]["content"]))
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return Completion(text=text, tokens=1, model=_ROUTE.model, provider="local", mode=_ROUTE.mode)

    monkeypatch.setattr("app.agent.generate.current_route", lambda: _ROUTE)
    monkeypatch.setattr("app.agent.generate.complete", complete)
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


def test_mock_provider_does_not_call_the_model(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("модель не должна вызываться")

    monkeypatch.setattr("app.agent.generate.complete", fail)
    assert request_copy(_context()) is None
    assert extract_fact(find_news("cbr-rate-14")) is None
