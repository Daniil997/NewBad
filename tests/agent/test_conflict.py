from types import SimpleNamespace

from app.agent.loop import build_card
from app.agent.verify import _lean, _sources_conflict
from app.corpus.search import BankDocument, NewsItem


def _card(fact: str, bank_quote: str = ""):
    return SimpleNamespace(
        what_happened=SimpleNamespace(claims=[SimpleNamespace(layer="confirmed_fact", text=fact)]),
        bank_view=SimpleNamespace(quote=bank_quote),
    )


def test_direction_is_searched_from_the_start_of_a_word():
    assert _lean("Всё это очень просто и понятно") is None
    assert _lean("Это вышедшие в прошлом году документы") is None
    assert _lean("Прирост выручки составил за квартал") == "up"
    assert _lean("Показатель стал выше прогноза") == "up"
    assert _lean("Ставка снижена, ниже прежнего уровня") == "down"
    assert _lean("Инфляция замедлилась, но рост цен на услуги сохраняется") == "both"


def test_a_news_text_with_both_directions_is_not_a_source_conflict():
    both = "Инфляция замедлилась, но рост цен на услуги сохраняется."
    assert _sources_conflict(_card(both)) is False
    assert _sources_conflict(_card(both, "Банк следит за динамикой цен.")) is False
    # Неоднозначный текст банка тоже не противоречие.
    assert _sources_conflict(_card("Рост цен ускорился.", "Цены то растут, то снижаются: рост и снижение.")) is False


def test_clearly_opposite_directions_are_still_a_conflict():
    assert _sources_conflict(_card("Банк России объявил повышение ставки.", "Банк ожидает снижения ставки.")) is True
    assert _sources_conflict(_card("Рост цен ускорился.", "Рост цен продолжится.")) is False


def test_card_for_a_news_with_both_directions_is_built_instead_of_failing(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    news = NewsItem(
        id="both-directions-news",
        url="https://example.test/inflation",
        title="Инфляция замедлилась, но рост цен на услуги сохраняется",
        published_at="2026-10-20",
        excerpt="Годовая инфляция замедлилась, при этом рост цен на услуги сохраняется.",
        event_type="monetary_policy",
        event_title="Ключевая ставка",
    )
    quote = "Банк следит за динамикой цен."
    bank = BankDocument(
        doc_id="neutral-bank",
        title="Обзор банка",
        published_at="2026-10-19",
        topic="key_rate",
        page=1,
        source_type="bank_analytics",
        theses=[quote],
        text=quote,
        quote=quote,
    )
    monkeypatch.setattr("app.agent.loop.find_news", lambda event_id: news if event_id == news.id else None)
    monkeypatch.setattr("app.agent.tools.find_news", lambda event_id: news if event_id == news.id else None)
    monkeypatch.setattr(
        "app.agent.tools.bank_analytics",
        lambda *_args, **_kwargs: {
            "hits": [{"id": bank.doc_id, "page": bank.page, "published_at": bank.published_at,
                      "title": bank.title, "quote": bank.quote, "source_type": bank.source_type}],
            "message": "",
        },
    )
    monkeypatch.setattr("app.agent.loop.find_bank", lambda doc_id, _page=None: bank if doc_id == bank.doc_id else None)

    card = build_card("U-BORROWER", news.id)

    assert card is not None
    assert card.verification.status == "passed"
