from datetime import date

import httpx

from app.ingest import news as ingest
from app.ingest.news import ImportedNews, NewsSource, import_news


SOURCE = NewsSource(id="coverage", name="Новости", url="https://example.org/rss", article_mode="html", min_interval_s=0)
BODY = "Официальная публикация содержит условия поддержки предприятий и развития производства. " * 30


def entry(number: int, text: str = "", scheme: str = "https") -> str:
    return (f"<item><title>Решение о поддержке предприятий {number}</title>"
            f"<link>{scheme}://example.org/news/{number}</link><guid>{number}</guid>"
            f"<pubDate>2026-10-24</pubDate><description>{text}</description></item>")


def rss(*items: str) -> str:
    return "<rss><channel>" + "".join(items) + "</channel></rss>"


def test_aspnet_form_and_global_navigation_do_not_hide_article():
    links = "".join(f'<a href="/section/{i}">Раздел сайта с дополнительными материалами {i}</a>' for i in range(40))
    page = (f'<html><head><title>Метаданные</title></head><body><form><input name="query">'
            f'<div>{links}</div><div class="text_block"><p>{BODY}</p></div>'
            '<footer>Контакты и навигация</footer></form></body></html>')
    text = ingest._html_to_text(page)
    assert text == BODY.strip()
    assert "Метаданные" not in text
    assert "Раздел сайта" not in text


def test_new_articles_get_budget_before_existing_short_summaries(tmp_path):
    known = ImportedNews(id="coverage-1", url="https://example.org/news/1", title="Решение о поддержке предприятий 1",
                         text=BODY[:150], published_at="2026-10-24", source=SOURCE.name,
                         loaded_at="2026-10-24", sha256="a" * 64, status="imported")
    fetched = []
    result = import_news(tmp_path, source=SOURCE.model_copy(update={"max_article_fetches": 1}),
                         fetch=lambda: rss(entry(1, known.text), entry(2)),
                         fetch_article=lambda url: fetched.append(url) or f"<article>{BODY}</article>",
                         known_items=[known], loaded_on=date(2026, 10, 25))
    assert fetched == ["https://example.org/news/2"]
    assert any(item.url.endswith("/2") and item.status == "imported" for item in result.items)
    assert result.deferred_articles == 1


def test_cached_full_text_does_not_spend_fetch_budget(tmp_path):
    known = ImportedNews(id="coverage-1", url="https://example.org/news/1", title="Решение о поддержке предприятий 1",
                         text=BODY, published_at="2026-10-24", source=SOURCE.name,
                         loaded_at="2026-10-24", sha256="a" * 64, status="imported")
    result = import_news(tmp_path, source=SOURCE, fetch=lambda: rss(entry(1)),
                         fetch_article=lambda _: (_ for _ in ()).throw(AssertionError("Unexpected fetch")),
                         known_items=[known], loaded_on=date(2026, 10, 25))
    assert result.article_fetches == 0
    assert result.items[0].status == "duplicate"
    assert result.items[0].text == BODY.strip()


def test_fetch_limit_and_page_failures_have_separate_diagnostics(tmp_path):
    def fail(_):
        raise httpx.ConnectError("Unavailable")

    result = import_news(tmp_path, source=SOURCE.model_copy(update={"max_article_fetches": 1}),
                         fetch=lambda: rss(entry(1), entry(2)), fetch_article=fail,
                         loaded_on=date(2026, 10, 25))
    assert result.article_fetches == result.article_errors == result.deferred_articles == 1
    assert {row.reason for row in result.rejected} == {
        "текст новости отсутствует или слишком короткий", "достигнут лимит загрузки страниц",
    }


def test_official_http_permalink_is_fetched_over_https(tmp_path):
    fetched = []
    result = import_news(tmp_path, source=SOURCE, fetch=lambda: rss(entry(1, scheme="http")),
                         fetch_article=lambda url: fetched.append(url) or f"<article>{BODY}</article>",
                         loaded_on=date(2026, 10, 25))
    assert fetched == ["https://example.org/news/1"]
    assert result.items[0].url == fetched[0]


def test_repeated_page_fragments_share_one_request(tmp_path):
    fetched = []
    payload = rss(entry(1), entry(2).replace("/news/2", "/news/1#details"))
    result = import_news(tmp_path, source=SOURCE, fetch=lambda: payload,
                         fetch_article=lambda url: fetched.append(url) or f"<article>{BODY}</article>",
                         loaded_on=date(2026, 10, 25))
    assert len(fetched) == result.article_fetches == 1
    assert len(result.items) == 2


def test_slow_source_has_a_bounded_article_budget(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(ingest.time, "monotonic", lambda: clock[0])

    def slow_article(_):
        clock[0] += 100
        return f"<article>{BODY}</article>"

    result = import_news(tmp_path, source=SOURCE, fetch=lambda: rss(entry(1), entry(2)),
                         fetch_article=slow_article, loaded_on=date(2026, 10, 25))
    assert result.article_fetches == 1
    assert result.deferred_by_time == result.deferred_articles == 1
    assert result.rejected[0].reason == "достигнут лимит времени загрузки страниц"


def test_semantic_duplicates_are_not_reinserted_under_another_url(monkeypatch, tmp_path):
    from app.news_store import imported_news

    monkeypatch.setattr(ingest, "load_news_sources", lambda: [SOURCE])
    today = date.today().isoformat()
    payload = rss(entry(1, BODY), entry(2, BODY).replace("предприятий 2", "предприятий 1"))
    monkeypatch.setattr(ingest, "_fetch_rss", lambda *_: payload.replace("2026-10-24", today))
    report = ingest.import_news_catalog(tmp_path)
    assert report.sources[0]["imported"] == 1
    assert report.sources[0]["duplicates"] == 1
    assert len(imported_news()) == 1
