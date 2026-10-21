from datetime import date

from app.corpus.search import NewsItem
from app.profile.build import get_profile
from app.scoring.feed import _entity_match, _score_feed, build_feed


def _factor(item, factor_id: str):
    found = [factor for factor in item.factors if factor.id == factor_id]
    return found[0] if found else None


def _item(feed, news_id: str):
    for item in feed.top + feed.less_relevant:
        if item.news_id == news_id:
            return item
    raise AssertionError(news_id)


def test_score_lists_the_factors_that_moved_the_item():
    feed = build_feed("U-BORROWER", today=date(2026, 10, 21))
    assert feed is not None
    rate = _item(feed, "cbr-rate-14")
    assert rate.band == "high"
    assert rate in feed.top
    assert {factor.id for factor in rate.factors} >= {"exposure", "bank", "source", "fresh", "gap"}
    assert _factor(rate, "industry") is None
    assert "подтверждено: Заёмное финансирование" in rate.reason
    assert "есть аналитика банка по этой теме" in rate.reason
    assert "штраф за пропуски: Регион" in rate.reason
    assert rate.score == round(sum(factor.value for factor in rate.factors), 4)

    oil = _item(feed, "gas-price")
    assert oil.band == "extra"
    assert oil not in feed.top
    assert _factor(oil, "industry") is not None
    assert _factor(oil, "exposure") is None
    assert "отрасль: Металлы" in oil.reason
    older = _item(feed, "brent-oil")
    assert older.band == "low"
    assert _factor(older, "duplicate") is not None

    peer = build_feed("U-PEER", today=date(2026, 10, 21))
    assert peer is not None
    peer_rate = _item(peer, "cbr-rate-14")
    assert peer_rate.band == "low"
    assert peer_rate not in peer.top
    assert rate.score > peer_rate.score


def test_confirmed_exposure_is_not_added_twice():
    feed = build_feed("U-BORROWER", today=date(2026, 10, 21))
    assert feed is not None
    item = _item(feed, "export-limits")
    assert {"foreign_trade", "country_exposure"} <= set(item.matched_features)
    exposure = _factor(item, "exposure")
    assert exposure is not None
    assert exposure.value == round(0.40 * 0.8, 4)
    assert _factor(item, "industry") is None


def test_country_in_the_text_is_not_scored_again():
    profile = get_profile("U-BORROWER")
    assert profile is not None
    assert _entity_match(profile, "поставки в китай", set()) == 0.8
    assert _entity_match(profile, "поставки в китай", {"country_exposure"}) == 0.0


def test_equal_scores_keep_a_stable_order(monkeypatch):
    def news():
        return [
            _news("b-id", "2026-09-11"),
            _news("a-id", "2026-09-11"),
            _news("c-id", "2026-09-01"),
        ]

    monkeypatch.setattr("app.scoring.feed.load_news", news)
    monkeypatch.setattr("app.scoring.feed.load_bank_documents", lambda: [])
    profile = get_profile("U-BORROWER")
    assert profile is not None
    first = _score_feed(profile, date(2026, 10, 21), "v")
    second = _score_feed(profile, date(2026, 10, 21), "v")
    assert [item.news_id for item in first.top] == ["b-id", "a-id"]
    assert [item.news_id for item in second.top] == [item.news_id for item in first.top]
    assert [item.news_id for item in second.less_relevant] == [item.news_id for item in first.less_relevant]
    assert _item(first, "c-id").band == "low"


def _news(news_id: str, published: str) -> NewsItem:
    return NewsItem(
        id=news_id,
        url="https://www.cbr.ru/press/",
        title="Ключевая ставка",
        published_at=published,
        excerpt="Совет директоров сохранил ключевую ставку.",
        event_type="monetary_policy",
        event_title="Ключевая ставка и денежно-кредитная политика",
        source="Банк России",
    )
