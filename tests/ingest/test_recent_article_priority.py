"""News ingestion must prioritize recent articles and preserve distinct updates."""

from datetime import date
from html import escape

import httpx
import pytest

from app.ingest import news as ingest
from app.ingest.news import NewsSource, import_news


SOURCE = NewsSource(
    id="fresh-first-test", name="Тестовая RSS-лента",
    url="https://www.cbr.ru/rss/RssPress", article_mode="html", min_interval_s=0,
)


def item(name: str, day: str, number: int) -> str:
    text = "Опубликованы новые официальные условия работы финансовых организаций и участников рынка."
    return (
        "<item><title>" + escape(name) + "</title>"
        "<link>https://www.cbr.ru/news/?id=" + str(number) + "</link>"
        "<guid>fresh_" + str(number) + "</guid>"
        "<pubDate>" + day + "</pubDate>"
        "<description>" + text + "</description></item>"
    )


def feed(*items: str) -> str:
    return "<rss><channel>" + "".join(items) + "</channel></rss>"


def test_stale_items_do_not_consume_full_article_fetch_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "_MAX_ARTICLE_FETCHES", 1)
    seen: list[str] = []
    article_body = "<article><p>" + ("Основные положения решения Банка России и условия применения. " * 12) + "</p></article>"

    def get_article(url: str) -> str:
        seen.append(url)
        return article_body

    rss = feed(
        item("Старое сообщение о рынке", "Fri, 11 Sep 2026 13:30:00 +0300", 1),
        item("Новое решение о рынке", "Sat, 24 Oct 2026 12:00:00 +0300", 2),
    )
    result = import_news(
        tmp_path, source=SOURCE, fetch=lambda: rss, fetch_article=get_article,
        loaded_on=date(2026, 10, 24), min_interval_s=0, max_age_days=14,
    )
    assert len(seen) == 1 and seen[0].endswith("id=2")
    assert len(result.items) == 1
    assert len(result.items[0].text) > 300
    assert result.rejected[0].reason == "старше окна сбора"


def test_newest_recent_items_receive_fetch_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "_MAX_ARTICLE_FETCHES", 1)
    seen: list[str] = []
    rss = feed(
        item("Прошлая новость о финансировании", "Thu, 22 Oct 2026 12:00:00 +0300", 3),
        item("Самая свежая новость о финансировании", "Sat, 24 Oct 2026 12:00:00 +0300", 4),
    )
    result = import_news(
        tmp_path, source=SOURCE, fetch=lambda: rss,
        fetch_article=lambda url: seen.append(url) or "<article><p>" + ("Текст публикации с фактами. " * 30) + "</p></article>",
        loaded_on=date(2026, 10, 24), min_interval_s=0, max_age_days=14,
    )
    assert seen == ["https://www.cbr.ru/news/?id=4"]
    assert len(result.items) == 2


def test_same_title_different_dates_is_not_falsely_deduplicated(tmp_path):
    rss = feed(
        item("Ежедневная информация об операциях финансового рынка", "Fri, 23 Oct 2026 12:00:00 +0300", 5),
        item("Ежедневная информация об операциях финансового рынка", "Sat, 24 Oct 2026 12:00:00 +0300", 6),
    )
    result = import_news(
        tmp_path, source=SOURCE.model_copy(update={"article_mode": "rss_only"}),
        fetch=lambda: rss, loaded_on=date(2026, 10, 24), min_interval_s=0,
    )
    assert len([item for item in result.items if item.status == "imported"]) == 2


def test_atom_entry_with_href_and_iso_date_is_supported(tmp_path):
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<id>tag:cbr.ru,2026:atom-1</id><title>Новое решение регулятора</title>"
        '<link rel="alternate" href="https://www.cbr.ru/press/event/?id=atom"/>'
        "<published>2026-10-24T12:00:00+03:00</published>"
        "<summary>Банк России представил новый документ с условиями работы на финансовом рынке.</summary>"
        "</entry></feed>"
    )
    result = import_news(
        tmp_path, source=SOURCE.model_copy(update={"article_mode": "rss_only"}),
        fetch=lambda: xml, loaded_on=date(2026, 10, 24), min_interval_s=0, max_age_days=14,
    )
    assert result.status == "imported"
    assert result.items[0].url.endswith("id=atom")
    assert result.items[0].published_at == "2026-10-24"


def test_similar_titles_on_same_day_preserve_distinct_articles(tmp_path):
    first = item("Банк России сообщил о новых условиях кредитования банков", "Sat, 24 Oct 2026 10:00:00 +0300", 7)
    second = (
        item("Банк России сообщил о новых условиях кредитования банков и компаний",
             "Sat, 24 Oct 2026 13:00:00 +0300", 8)
        .replace(
            "Опубликованы новые официальные условия работы финансовых организаций и участников рынка.",
            "Опубликованы отдельные изменения требований к участникам рынка капитала и платежным системам.",
        )
    )
    result = import_news(
        tmp_path, source=SOURCE.model_copy(update={"article_mode": "rss_only"}),
        fetch=lambda: feed(first, second), loaded_on=date(2026, 10, 24), min_interval_s=0,
    )
    assert len([row for row in result.items if row.status == "imported"]) == 2


def test_escaped_markup_is_cleaned_after_entity_decoding():
    text = ingest._html_to_text("&lt;p&gt;Важный текст официального документа.&lt;/p&gt;")
    assert text == "Важный текст официального документа."
    assert "<p>" not in text


def test_redirects_are_allowed_only_within_original_official_https_host(monkeypatch):
    seen: list[str] = []

    def fake_get(url: str, **_kwargs):
        seen.append(url)
        if url == "https://www.cbr.ru/rss/RssPress":
            return httpx.Response(
                301, headers={"location": "https://cbr.ru/rss/RssPress"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200, text="<rss><channel></channel></rss>", request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ingest.httpx, "get", fake_get)
    assert "<rss>" in ingest._fetch_rss("https://www.cbr.ru/rss/RssPress", 5)
    assert seen == ["https://www.cbr.ru/rss/RssPress", "https://cbr.ru/rss/RssPress"]


def test_redirect_to_foreign_host_is_blocked_before_request(monkeypatch):
    seen: list[str] = []

    def fake_get(url: str, **_kwargs):
        seen.append(url)
        return httpx.Response(
            302, headers={"location": "https://example.org/other"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ingest.httpx, "get", fake_get)
    with pytest.raises(ingest.NewsIngestError, match="перенаправление"):
        ingest._fetch_rss("https://www.cbr.ru/rss/RssPress", 5)
    assert seen == ["https://www.cbr.ru/rss/RssPress"]


def test_catalog_diagnostics_distinguish_age_from_invalid_news(tmp_path, monkeypatch):
    from datetime import timedelta

    fresh = date.today()
    old = fresh - timedelta(days=40)
    formatted = lambda day: day.strftime("%a, %d %b %Y 12:00:00 +0300")
    xml = feed(
        item("Старое решение Банка России", formatted(old), 27),
        item("Актуальное решение Банка России", formatted(fresh), 28),
    )
    monkeypatch.setattr(ingest, "load_news_sources", lambda: [
        SOURCE.model_copy(update={"article_mode": "rss_only"})
    ])
    monkeypatch.setattr(ingest, "_fetch_rss", lambda _url, _timeout: xml)
    report = ingest.import_news_catalog(tmp_path / "unused", max_age_days=14)
    row = report.sources[0]
    assert row["seen"] == 2
    assert row["skipped_old"] == 1
    assert row["rejected"] == 0
    assert row["imported"] == 1
    assert row["article_fetches"] == 0


def test_old_only_feed_is_not_reported_as_broken(tmp_path):
    old_rss = feed(item(
        "Давнее решение регулятора",
        "Fri, 11 Sep 2026 13:30:00 +0300", 99,
    ))
    batch = import_news(
        tmp_path, source=SOURCE.model_copy(update={"article_mode": "rss_only"}),
        fetch=lambda: old_rss, loaded_on=date(2026, 10, 24),
        min_interval_s=0, max_age_days=14,
    )
    assert batch.status == "outside_window"
    assert not batch.items
    assert batch.seen == 1
    assert batch.rejected[0].reason == "старше окна сбора"
