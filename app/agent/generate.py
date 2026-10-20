import json
import re

import yaml

from app.agent.loop_context import AgentContext
from app.corpus.search import NewsItem
from app.db import ROOT
from app.llm.client import LLMError, complete, parse_json
from app.llm.router import current_route

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def extract_fact(news: NewsItem) -> str | None:
    route = current_route()
    if route.provider != "local":
        return None
    source = f"{news.title}\n{news.excerpt}"
    try:
        result = complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Извлеки один короткий факт из новости. Верни JSON {\"fact\": \"...\"}. "
                        "Не добавляй числа и даты, которых нет в тексте."
                    ),
                },
                {"role": "user", "content": source},
            ],
            model=route.model,
            mode=route.mode,
            max_tokens=180,
        )
        fact = str(parse_json(result.text).get("fact") or "").strip()
    except LLMError:
        return None
    if not fact or not _grounded(fact, source):
        return None
    return fact


def request_copy(ctx: AgentContext) -> dict | None:
    route = current_route()
    if route.provider != "local" or ctx.profile is None or ctx.news is None:
        return None
    payload = {
        "news": ctx.extracted_fact or ctx.news.excerpt,
        "bank_quote": None if ctx.bank is None else ctx.bank.quote,
        "features": _prompt_features(ctx),
        "matched": ctx.matched,
        "issues": ctx.issues,
    }
    try:
        result = complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Собери вывод для карточки. Верни JSON с полями why, risk, opportunity, next_step, question. "
                        "Пиши по-русски, коротко. Не выдумывай числа: бери их только из news и bank_quote. "
                        "Цитату банка не переписывай."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            model=route.model,
            mode=route.mode,
            max_tokens=400,
        )
        copy = parse_json(result.text)
    except LLMError:
        return None
    if not all(str(copy.get(key) or "").strip() for key in ("why", "risk", "opportunity", "next_step", "question")):
        return None
    return {key: str(copy[key]).strip() for key in ("why", "risk", "opportunity", "next_step", "question")}


def answer_with_model(question: str, card_text: str) -> str | None:
    route = current_route()
    if route.provider != "local":
        return None
    try:
        result = complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Ответь на вопрос только по тексту карточки. Не добавляй чисел, которых в карточке нет. "
                        "Если ответа нет, так и скажи."
                    ),
                },
                {"role": "user", "content": json.dumps({"question": question, "card": card_text}, ensure_ascii=False)},
            ],
            model=route.model,
            mode=route.mode,
            max_tokens=250,
        )
    except LLMError:
        return None
    text = result.text.strip()
    if not text or not _grounded(text, card_text):
        return None
    return text


def _prompt_features(ctx: AgentContext) -> list[dict[str, str]]:
    allowed = set(yaml.safe_load((ROOT / "config" / "prompt_fields.yaml").read_text(encoding="utf-8"))["allowed"])
    profile = ctx.profile
    assert profile is not None
    return [
        {"label": feature.label, "value": feature.value}
        for feature in profile.features
        if feature.id in allowed
    ]


def _grounded(text: str, source: str) -> bool:
    return _numbers(text) <= _numbers(source)


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", ".") for match in _NUMBER.finditer(text)}
