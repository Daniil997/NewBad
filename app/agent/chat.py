from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.agent.generate import answer_with_model, answered_mode
from app.agent.tools import run_tool
from app.corpus.search import CorpusError
from app.db import JournalError, connect, init_db
from app.schemas.card import Card
from app.tracing.journal import log_llm_call, trace_scope

_MAX_SEARCHES = 2
_MAX_TURNS = 8
_FOLLOW = ("повтори", "что ты сказал", "как ты ответил", "предыдущ")
_SEARCH = ("найди", "поищи", "другие материал", "что еще", "еще источник")
_SKIP = frozenset(
    "найди поищи другие материал материалы банка банке какие какой какая какие еще источник источники "
    "про это этой этом вопрос карточке компании".split()
)


class Citation(BaseModel):
    source_id: str
    title: str
    url: str = ""
    page: int | None = Field(default=None, ge=1)
    quote: str = ""


class ChatReply(BaseModel):
    answer: str
    mode: str
    source_ids: list[str]
    citations: list[Citation] = Field(default_factory=list)


def answer_from_card(
    card: Card,
    question: str,
    client_id: str | None = None,
    event_id: str | None = None,
) -> ChatReply:
    bound_client = client_id or card.client_id
    bound_event = event_id or card.event_id
    if bound_client != card.client_id or bound_event != card.event_id:
        raise ValueError("Вопрос не относится к выбранной карточке")
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("Пустой вопрос")
    history = _history(bound_client, bound_event)
    if _is_followup(cleaned):
        return _remember(card, cleaned, _follow(history))
    if _wants_search(cleaned) and not _covered(cleaned, card):
        return _remember(card, cleaned, _search(card, cleaned))
    return _remember(card, cleaned, _from_card(card, cleaned, history))


def _follow(history: list[tuple[str, str]]) -> tuple[str, str, list[Citation]]:
    prior = [text for role, text in history if role == "assistant"]
    if not prior:
        return "Контекст диалога по этой карточке пуст.", "mock", []
    return prior[-1], "mock", []


def _from_card(card: Card, question: str, history: list[tuple[str, str]]) -> tuple[str, str, list[Citation]]:
    with trace_scope(card.client_id, card.event_id):
        model_answer = answer_with_model(question, _card_text(card, history), _evidence(card))
        if model_answer:
            citations = _citations_in_text(card, model_answer)
            return _decorate(model_answer, citations), answered_mode(), citations
        log_llm_call(
            "mock-model",
            "mock",
            "mock",
            tokens=0,
            latency_ms=0,
            switch_reason="чат отвечает по карточке",
        )
    text = _pick(card, question.lower().replace("ё", "е"))
    citations = _citations_in_text(card, text)
    return _decorate(text, citations), "mock", citations


def _search(card: Card, question: str) -> tuple[str, str, list[Citation]]:
    if _search_count(card.client_id, card.event_id) >= _MAX_SEARCHES:
        return "Дополнительный поиск по этой карточке уже выполнен.", "mock", []
    tool = "search_bank_analytics" if "банк" in question.lower() else "search_news"
    query = question.strip()[:200]
    _remember_search(card, query)
    try:
        with trace_scope(card.client_id, card.event_id):
            result = run_tool(tool, {"query": query})
    except (ValueError, CorpusError, JournalError):
        return "Инструмент поиска не ответил. Ответ остаётся по открытой карточке.", "mock", []
    return _search_reply(result)


def _search_reply(result: dict) -> tuple[str, str, list[Citation]]:
    items = result.get("items") or []
    if result.get("status") != "ok" or not items:
        return "Дополнительный поиск не нашёл материалов.", "mock", []
    item = items[0]
    quote = str(item.get("quote") or item.get("excerpt") or "").strip()
    title = str(item.get("title") or item.get("evidence_id") or "материал")
    page = item.get("page") if isinstance(item.get("page"), int) and item["page"] >= 1 else None
    citation = Citation(
        source_id=str(item.get("id") or item.get("evidence_id") or title),
        title=title,
        url=str(item.get("url") or ""),
        page=page,
        quote=quote,
    )
    text = quote or f"Найден материал «{title}»."
    return _decorate(text, [citation]), "mock", [citation]


def _pick(card: Card, question: str) -> str:
    if "профил" in question or "признак" in question:
        if not card.profile_facts:
            return "Факты профиля в карточке не указаны."
        listed = "; ".join(f"{item.label}: {item.value}" for item in card.profile_facts)
        return f"Факты профиля: {listed}."
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


def _card_text(card: Card, history: list[tuple[str, str]]) -> str:
    parts = [claim.text for claim in card.all_claims()]
    parts.extend(f"{item.label}: {item.value}" for item in card.profile_facts)
    parts.extend([card.action.next_step, card.bank_view.quote, *card.action.questions])
    for source in card.sources:
        if source.quote:
            parts.append(source.quote)
        if source.url:
            parts.append(source.url)
    for role, text in history:
        parts.append(f"{role}: {text}")
    return " ".join(part for part in parts if part)


def _evidence(card: Card) -> list[dict]:
    packet = []
    for source in card.sources:
        if source.kind not in {"news", "bank_analytics"}:
            continue
        packet.append(
            {
                "evidence_id": source.id,
                "source": source.kind,
                "title": source.title,
                "text": source.quote or source.title,
                "url": source.url or "",
                "page": source.page,
            }
        )
    for fact in card.profile_facts:
        packet.append(
            {
                "evidence_id": fact.feature_id,
                "source": "company_feature",
                "title": fact.label,
                "text": f"{fact.label}: {fact.value}",
            }
        )
    return packet


def _citations_in_text(card: Card, text: str) -> list[Citation]:
    found: list[Citation] = []
    for source in card.sources:
        if source.kind not in {"news", "bank_analytics"} or not source.quote or source.quote not in text:
            continue
        found.append(
            Citation(
                source_id=source.id,
                title=source.title,
                url=source.url or "",
                page=source.page,
                quote=source.quote,
            )
        )
    return found


def _decorate(text: str, citations: list[Citation]) -> str:
    if not citations:
        return text
    parts = []
    for item in citations:
        bits = [item.title]
        if item.page:
            bits.append(f"стр. {item.page}")
        if item.url:
            bits.append(item.url)
        parts.append(", ".join(bits))
    return f"{text} Источник: {'; '.join(parts)}."


def _source_ids(card: Card) -> list[str]:
    return [item.id for item in card.sources if item.kind in {"news", "bank_analytics"}]


def _is_followup(question: str) -> bool:
    folded = question.lower().replace("ё", "е")
    return any(phrase in folded for phrase in _FOLLOW)


def _wants_search(question: str) -> bool:
    folded = question.lower().replace("ё", "е")
    return any(phrase in folded for phrase in _SEARCH)


def _covered(question: str, card: Card) -> bool:
    haystack = _card_text(card, []).lower().replace("ё", "е")
    words = [word for word in _words(question) if word not in _SKIP]
    return bool(words) and all(word in haystack for word in words)


def _words(question: str) -> list[str]:
    folded = question.lower().replace("ё", "е")
    word = []
    words: list[str] = []
    for char in folded:
        if char.isalpha():
            word.append(char)
            continue
        if len(word) >= 5:
            words.append("".join(word))
        word = []
    if len(word) >= 5:
        words.append("".join(word))
    return words


def _history(client_id: str, event_id: str) -> list[tuple[str, str]]:
    try:
        init_db()
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT role, text FROM chat_turns
                WHERE client_id = ? AND event_id = ? AND role IN ('user', 'assistant')
                ORDER BY id DESC
                LIMIT ?
                """,
                (client_id, event_id, _MAX_TURNS),
            ).fetchall()
    except (JournalError, OSError) as error:
        raise ValueError("Контекст диалога недоступен") from error
    return [(row["role"], row["text"]) for row in reversed(rows)]


def _search_count(client_id: str, event_id: str) -> int:
    init_db()
    with connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS n FROM chat_turns
            WHERE client_id = ? AND event_id = ? AND role = 'search'
            """,
            (client_id, event_id),
        ).fetchone()
    return int(row["n"])


def _remember_search(card: Card, query: str) -> None:
    _insert(card.client_id, card.event_id, "search", query)


def _remember(card: Card, question: str, outcome: tuple[str, str, list[Citation]]) -> ChatReply:
    text, mode, citations = outcome
    _insert(card.client_id, card.event_id, "user", question)
    _insert(card.client_id, card.event_id, "assistant", text)
    _trim(card.client_id, card.event_id)
    source_ids = list(dict.fromkeys([*_source_ids(card), *(item.source_id for item in citations)]))
    return ChatReply(answer=text, mode=mode, source_ids=source_ids, citations=citations)


def _insert(client_id: str, event_id: str, role: str, text: str) -> None:
    try:
        init_db()
        with connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_turns (client_id, event_id, role, text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (client_id, event_id, role, text, datetime.now(timezone.utc).isoformat()),
            )
    except JournalError as error:
        raise ValueError("Контекст диалога недоступен") from error


def _trim(client_id: str, event_id: str) -> None:
    with connect() as connection:
        connection.execute(
            """
            DELETE FROM chat_turns
            WHERE client_id = ? AND event_id = ? AND role IN ('user', 'assistant')
              AND id NOT IN (
                SELECT id FROM chat_turns
                WHERE client_id = ? AND event_id = ? AND role IN ('user', 'assistant')
                ORDER BY id DESC
                LIMIT ?
              )
            """,
            (client_id, event_id, client_id, event_id, _MAX_TURNS),
        )
