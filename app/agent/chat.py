from pydantic import BaseModel

from app.agent.generate import answer_with_model, answered_mode
from app.schemas.card import Card
from app.tracing.journal import log_llm_call, trace_scope


class ChatReply(BaseModel):
    answer: str
    mode: str
    source_ids: list[str]


def answer_from_card(card: Card, question: str) -> ChatReply:
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("Пустой вопрос")
    source_ids = [item.id for item in card.sources if item.kind in {"news", "bank_analytics"}]
    with trace_scope(card.client_id, card.event_id):
        model_answer = answer_with_model(cleaned, _card_text(card))
        if model_answer:
            return ChatReply(answer=model_answer, mode=answered_mode(), source_ids=source_ids)
        log_llm_call("mock-model", "mock", "mock", tokens=0, latency_ms=0, switch_reason="чат отвечает по карточке")
        return ChatReply(answer=_pick(card, cleaned.lower()), mode="mock", source_ids=source_ids)


def _card_text(card: Card) -> str:
    parts = [claim.text for claim in card.all_claims()]
    parts.extend([card.action.next_step, card.bank_view.quote, *card.action.questions])
    return " ".join(parts)


def _pick(card: Card, question: str) -> str:
    why = " ".join(item.text for item in card.why_for_company.claims)
    if "банк" in question or "цитат" in question:
        if card.bank_view.status == "found" and card.bank_view.quote:
            return card.bank_view.quote
        return "Позиции банка по этой новости нет."
    if any(phrase in question for phrase in ("что сделать", "какой шаг", "какое действие", "следующий шаг")):
        return card.action.next_step
    if "не хвата" in question or "пропуск" in question:
        if card.missing_data:
            return "Не хватает данных: " + ", ".join(card.missing_data)
        return "Пропусков в профиле нет."
    if "срок" in question and card.assumptions:
        return card.assumptions[0]
    if card.bank_view.quote:
        return f"{why} Позиция банка: {card.bank_view.quote}"
    return why
