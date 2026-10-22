import json
import re
from contextvars import ContextVar

import yaml

from app.agent.loop_context import AgentContext
from app.corpus.search import NewsItem
from app.db import ROOT
from app.llm.client import LLMError, parse_json
from app.llm.router import Route, complete_tier, token_limit

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_FACT = "confirmed_fact"
_BANK = "bank_position"
_INFERENCE = "model_inference"
_COPY_FIELDS = ("why", "risk", "opportunity", "next_step", "question")
_ANSWER_MODE: ContextVar[str] = ContextVar("answer_mode", default="mock")


def extract_fact(news: NewsItem) -> str | None:
    packet = [_news_evidence(news)]
    payload, _route, _tokens = _ask(
        _messages(
            (
                "Извлеки один короткий факт. Верни JSON с полями text, layer и evidence_ids. "
                "layer только confirmed_fact. evidence_ids — только id из evidence. "
                "Не добавляй числа, которых нет в text доказательства, и не указывай другие источники."
            ),
            {"evidence": packet},
        ),
        token_limit("fact"),
    )
    claim = None if payload is None else _claim(payload, _index(packet), _FACT)
    return None if claim is None else claim["text"]


def request_copy(ctx: AgentContext) -> dict | None:
    _mark(ctx, "template", None, 0)
    if ctx.profile is None or ctx.news is None:
        return None
    packet = evidence_packet(ctx)
    allowed = _index(packet)
    news_ids = [item["evidence_id"] for item in packet if item["source"] == "news"]
    bank_ids = [item["evidence_id"] for item in packet if item["layer"] == _BANK]
    inference_ids = [item["evidence_id"] for item in packet if item["layer"] in {_FACT, _BANK}]
    payload, route, tokens = _ask(
        _messages(
            (
                "Верни только один JSON-объект строго такой формы, без markdown и других ключей: "
                '{"fact":{"text":"короткий факт из новости","layer":"confirmed_fact",'
                '"evidence_ids":["ID_НОВОСТИ"]},"bank":null или '
                '{"text":"дословная цитата банка","layer":"bank_position","evidence_ids":["ID_БАНКА"]},'
                '"inference":{"layer":"model_inference","evidence_ids":["ID_НОВОСТИ","ID_ПРИЗНАКА"],'
                '"why":"...","risk":"...","opportunity":"...","next_step":"...","question":"..."}}. '
                f"Допустимые ID: fact={news_ids}; bank={bank_ids}; inference={inference_ids}. "
                "В fact укажи только ID новости и процитируй короткое предложение из неё. "
                "В bank укажи только ID банковской страницы и точную подстроку её цитаты; если банковской страницы нет, bank=null. "
                "Для inference выбери только нужные ID, не перечисляй весь список. "
                "Не добавляй неподтверждённые числа, даты, причины или утверждения."
            ),
            {"evidence": packet, "issues": ctx.issues},
        ),
        token_limit("copy"),
    )
    if payload is None or any(key not in payload for key in ("fact", "bank", "inference")):
        return None
    fact = _claim(payload.get("fact"), allowed, _FACT)
    if fact is None:
        return None
    has_bank = any(item["layer"] == _BANK for item in packet)
    if payload.get("bank") is None:
        bank = None
    elif not has_bank:
        return None
    else:
        bank = _claim(payload.get("bank"), allowed, _BANK)
        if bank is None:
            return None
    inference = payload.get("inference")
    if not isinstance(inference, dict) or inference.get("layer") != _INFERENCE:
        return None
    ids = _known_ids(inference.get("evidence_ids"), allowed)
    if not ids:
        return None
    corpus = _cited_text(ids, allowed)
    fields: dict[str, str] = {}
    for key in _COPY_FIELDS:
        text = str(inference.get(key) or "").strip()
        if not text or not _grounded(text, corpus):
            return None
        fields[key] = text
    _mark(ctx, _origin(route), route, tokens)
    return {**fields, "fact": fact, "bank": bank, "inference": {"layer": _INFERENCE, "evidence_ids": ids, **fields}}


def answer_with_model(question: str, card_text: str, evidence: list[dict] | None = None) -> str | None:
    packet = evidence if evidence is not None else [
        {"evidence_id": "card", "source": "card", "layer": _FACT, "title": "Карточка", "text": card_text}
    ]
    payload, route, _tokens = _ask(
        _messages(
            (
                "Ответь на вопрос только по evidence. Верни JSON {\"answer\": \"...\", \"evidence_ids\": [...]}. "
                "evidence_ids бери только из переданного списка. Если в ответе есть число, укажи id доказательства, "
                "в котором оно есть. Не создавай новые источники."
            ),
            {"question": question, "evidence": packet},
        ),
        token_limit("answer"),
    )
    if payload is None:
        return None
    answer = str(payload.get("answer") or "").strip()
    allowed = _index(packet)
    ids = _known_ids(payload.get("evidence_ids"), allowed)
    if not answer or ids is None:
        return None
    corpus = _cited_text(ids, allowed) if ids else ""
    if _numbers(answer) and not ids:
        return None
    if not _grounded(answer, corpus or card_text):
        return None
    _ANSWER_MODE.set("replay" if _origin(route) == "replay" else route.mode)
    return answer


def answered_mode() -> str:
    return _ANSWER_MODE.get()


def evidence_packet(ctx: AgentContext) -> list[dict]:
    items: list[dict] = []
    if ctx.news is not None:
        items.append(_news_evidence(ctx.news, ctx.extracted_fact))
    if ctx.bank is not None:
        items.append(
            {
                "evidence_id": f"{ctx.bank.doc_id}:{ctx.bank.page}",
                "source": ctx.bank.source_type,
                "layer": _BANK,
                "title": ctx.bank.title,
                "page": ctx.bank.page,
                "text": ctx.bank.quote,
            }
        )
    for feature in _prompt_features(ctx):
        items.append(
            {
                "evidence_id": feature["id"],
                "source": "company_profile",
                "layer": _FACT,
                "title": feature["label"],
                "text": feature["value"],
            }
        )
    return items


def _news_evidence(news: NewsItem, text: str | None = None) -> dict:
    return {
        "evidence_id": news.id,
        "source": news.url or news.source or "news",
        "layer": _FACT,
        "title": news.title,
        "text": text or f"{news.title}. {news.excerpt}",
    }


def _ask(messages: list[dict[str, str]], max_tokens: int) -> tuple[dict | None, Route, int]:
    outcome = complete_tier(messages, tier="heavy", max_tokens=max_tokens)
    if not outcome.text:
        return None, outcome.route, 0
    try:
        return parse_json(outcome.text), outcome.route, outcome.tokens
    except LLMError:
        return None, outcome.route, 0


def _origin(route: Route) -> str:
    if route.mode == "replay" or route.provider == "replay":
        return "replay"
    if route.provider in {"local", "cloud"}:
        return "model"
    return "template"


def _mark(ctx: AgentContext, origin: str, route: Route | None, tokens: int) -> None:
    ctx.generation_origin = origin
    if route is None or origin == "template":
        ctx.generation_mode = ""
        ctx.generation_provider = ""
        ctx.generation_model = ""
        ctx.generation_tokens = 0
        return
    ctx.generation_mode = route.mode
    ctx.generation_provider = route.provider
    ctx.generation_model = route.model
    ctx.generation_tokens = tokens


def _messages(instruction: str, payload: dict) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _claim(payload: object, allowed: dict[str, dict], layer: str) -> dict | None:
    if not isinstance(payload, dict) or payload.get("layer") != layer:
        return None
    if "source" in payload and str(payload.get("source") or "") not in {item["source"] for item in allowed.values()}:
        return None
    text = str(payload.get("text") or "").strip()
    ids = _known_ids(payload.get("evidence_ids"), allowed)
    if not text or not ids:
        return None
    if any(allowed[item]["layer"] != layer for item in ids):
        return None
    corpus = _cited_text(ids, allowed)
    if not _grounded(text, corpus):
        return None
    if layer == _BANK and not any(text in allowed[item]["text"] for item in ids):
        return None
    return {"text": text, "layer": layer, "evidence_ids": ids}


def _known_ids(value: object, allowed: dict[str, dict]) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) or item not in allowed for item in value):
        return None
    return value


def _cited_text(ids: list[str], allowed: dict[str, dict]) -> str:
    return " ".join(allowed[item]["text"] for item in ids)


def _index(items: list[dict]) -> dict[str, dict]:
    return {str(item["evidence_id"]): item for item in items}


def _prompt_features(ctx: AgentContext) -> list[dict[str, str]]:
    allowed = set(yaml.safe_load((ROOT / "config" / "prompt_fields.yaml").read_text(encoding="utf-8"))["allowed"])
    profile = ctx.profile
    if profile is None:
        return []
    return [
        {"id": feature.id, "label": feature.label, "value": feature.value}
        for feature in profile.features
        if feature.id in allowed
    ]


def _grounded(text: str, source: str) -> bool:
    return _numbers(text) <= _numbers(source)


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", ".") for match in _NUMBER.finditer(text)}
