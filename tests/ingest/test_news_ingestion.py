import html
from datetime import date
from urllib.parse import urlsplit

import httpx
import pytest

from app.corpus.search import search_news
from app.ingest.news import (
    ImportedNews,
    NewsIngestError,
    NewsSource,
    import_news,
    import_news_catalog,
    load_news_source,
    load_news_sources,
    prune_imported_news,
)

_SOURCE = NewsSource(
    id="cbr_press",
    name="Банк России",
    url="https://www.cbr.ru/rss/RssPress",
    timeout_s=5,
    min_interval_s=0,
)
_TITLE = "Банк России сохранил ключевую ставку на прежнем уровне"
_TEXT = "Совет директоров сохранил ключевую ставку. Стоимость заёмных денег пока не меняется."


def _feed(*items: str) -> str:
    body = "".join(items)
    return f"<?xml version='1.0' encoding='utf-8'?><rss version='2.0'><channel>{body}</channel></rss>"


def _item(
    title: str = _TITLE,
    url: str = "https://www.cbr.ru/press/event/?id=1",
    guid: str = "docid_1",
    published: str = "Fri, 11 Sep 2026 13:30:00 +0300",
    text: str = _TEXT,
) -> str:
    return (
        "<item>"
        f"<title>{title}</title><link>{url}</link><guid>{guid}</guid>"
        f"<pubDate>{published}</pubDate><description>{html.escape(text)}</description>"
        "</item>"
    )


def test_configured_source_is_the_cbr_press_feed():
    source = load_news_source()
    assert source.id == "cbr_press"
    assert source.name == "Банк России — пресс-релизы"
    assert urlsplit(source.url).netloc == "www.cbr.ru"
    assert source.url.endswith("/rss/RssPress")
    sources = {item.id: item for item in load_news_sources()}
    assert set(sources) == {"cbr_press", "cbr_events", "cbr_news", "cbr_currency", "fns_news",
                            "vedomosti_economics", "vedomosti_business", "vedomosti_finance"}
    assert all(source.article_mode == "rss_only" for key, source in sources.items() if key.startswith("vedomosti"))


def test_import_normalizes_and_stores_checksum(tmp_path):
    result = import_news(
        tmp_path,
        source=_SOURCE,
        fetch=lambda: _feed(_item(text=f"<p>{_TEXT}</p>")),
        loaded_on=date(2026, 10, 21),
        min_interval_s=0,
    )
    assert result.status == "imported"
    item = result.items[0]
    assert item.source == "Банк России"
    assert item.published_at == "2026-09-11"
    assert item.loaded_at == "2026-10-21"
    assert item.text == _TEXT
    assert item.url.startswith("https://www.cbr.ru/")
    assert len(item.sha256) == 64
    saved = (tmp_path / f"{item.id}.json").read_text(encoding="utf-8")
    assert item.sha256 in saved
    assert "imported" in saved
    assert (tmp_path / "import.json").is_file()


def test_duplicate_url_and_similar_title_are_not_imported_twice(tmp_path):
    first = _item()
    same_url = _item(guid="docid_2", title="Другое сообщение о курсе доллара на бирже")
    similar = _item(
        url="https://www.cbr.ru/press/event/?id=3",
        guid="docid_3",
        title=_TITLE + " для компаний",
    )
    result = import_news(
        tmp_path,
        source=_SOURCE,
        fetch=lambda: _feed(first, same_url, similar),
        loaded_on=date(2026, 10, 21),
        min_interval_s=0,
    )
    imported = [item for item in result.items if item.status == "imported"]
    duplicates = [item for item in result.items if item.status == "duplicate"]
    assert len(imported) == 1
    assert {item.duplicate_of for item in duplicates} == {imported[0].id}
    assert len(list(tmp_path.glob("cbr-*.json"))) == 1

    again = import_news(
        tmp_path,
        source=_SOURCE,
        fetch=lambda: _feed(first),
        loaded_on=date(2026, 10, 21),
        min_interval_s=0,
    )
    assert again.status == "duplicate"
    assert again.items[0].status == "duplicate"


def test_missing_date_url_and_text_are_not_invented(tmp_path):
    payload = _feed(
        _item(published=""),
        _item(url="не адрес", guid="docid_4", title="Сообщение без адреса источника"),
        _item(text="   ", guid="docid_5", url="https://www.cbr.ru/press/event/?id=5", title="Коротко"),
        _item(
            published="вчера",
            guid="docid_7",
            url="https://www.cbr.ru/press/event/?id=7",
            title="Сообщение с неразборчивой датой публикации",
        ),
        "<item><link>https://www.cbr.ru/press/event/?id=6</link><guid>docid_6</guid>"
        "<pubDate>Fri, 11 Sep 2026 13:30:00 +0300</pubDate><description>Текст</description></item>",
    )
    result = import_news(
        tmp_path,
        source=_SOURCE,
        fetch=lambda: payload,
        loaded_on=date(2026, 10, 21),
        min_interval_s=0,
    )
    assert result.status == "rejected"
    assert result.items == []
    reasons = {item.reason for item in result.rejected}
    assert reasons == {"нет или некорректна дата", "некорректный URL", "текст новости отсутствует или слишком короткий", "нет заголовка"}
    assert list(tmp_path.glob("cbr-*.json")) == []


def test_item_with_only_a_title_is_rejected_instead_of_fabricating_body(tmp_path):
    title = "23 октября состоится пресс-конференция Совета директоров по денежно-кредитной политике"
    result = import_news(
        tmp_path,
        source=_SOURCE,
        fetch=lambda: _feed(_item(title=title, text="", published="Fri, 23 Oct 2026 15:00:00 +0300")),
        loaded_on=date(2026, 10, 23),
        min_interval_s=0,
    )
    assert result.status == "rejected"
    assert result.items == []
    assert result.rejected[0].reason == "текст новости отсутствует или слишком короткий"


def test_empty_feed_is_not_a_source_error(tmp_path):
    result = import_news(
        tmp_path,
        source=_SOURCE,
        fetch=lambda: _feed(),
        min_interval_s=0,
    )
    assert result.status == "empty"
    assert result.items == []
    assert result.rejected == []


def test_source_failure_timeout_and_rate_are_errors(tmp_path):
    def timed_out() -> str:
        raise httpx.TimeoutException("timeout")

    def unavailable() -> str:
        raise httpx.ConnectError("нет сети")

    with pytest.raises(NewsIngestError) as timeout:
        import_news(tmp_path, source=_SOURCE, fetch=timed_out, min_interval_s=0)
    assert timeout.value.kind == "timeout"
    assert not (tmp_path / "import.json").exists()

    with pytest.raises(NewsIngestError) as source:
        import_news(tmp_path, source=_SOURCE, fetch=unavailable, min_interval_s=0)
    assert source.value.kind == "source"

    with pytest.raises(NewsIngestError) as broken:
        import_news(tmp_path, source=_SOURCE, fetch=lambda: "<rss>", min_interval_s=0)
    assert broken.value.kind == "source"

    limited = _SOURCE.model_copy(update={"id": "cbr_press_rate"})
    import_news(tmp_path / "once", source=limited, fetch=lambda: _feed(_item()), min_interval_s=60)
    with pytest.raises(NewsIngestError) as rate:
        import_news(tmp_path / "twice", source=limited, fetch=lambda: _feed(_item()), min_interval_s=60)
    assert rate.value.kind == "rate"


def test_imported_news_is_searchable_by_its_source_url(tmp_path):
    folder = tmp_path / "news"
    imported = import_news(
        folder,
        source=_SOURCE,
        fetch=lambda: _feed(_item()),
        loaded_on=date(2026, 10, 21),
        min_interval_s=0,
    )
    hits = search_news("ключевую ставку", folder=folder, today=date(2026, 10, 21))
    assert len(hits) == 1
    hit = hits[0]
    assert hit.id == imported.items[0].id
    assert hit.url == "https://www.cbr.ru/press/event/?id=1"
    assert hit.published_at == "2026-09-11"
    assert hit.event_type == "monetary_policy"
    assert "заёмных денег" in hit.excerpt

    duplicate = import_news(
        folder,
        source=_SOURCE,
        fetch=lambda: _feed(_item()),
        loaded_on=date(2026, 10, 21),
        min_interval_s=0,
    )
    assert duplicate.status == "duplicate"
    again = search_news("ключевую ставку", folder=folder, today=date(2026, 10, 21))
    assert [item.id for item in again] == [hit.id]


def test_import_rejects_items_outside_the_configured_age_window(tmp_path):
    result = import_news(
        tmp_path, source=_SOURCE, fetch=lambda: _feed(_item()),
        loaded_on=date(2026, 10, 21), max_age_days=3, min_interval_s=0,
    )
    assert result.items == []
    assert result.rejected[0].reason == "старше окна сбора"
    assert not list(tmp_path.glob("cbr-*.json"))


def test_retention_prunes_old_items_and_then_enforces_count(tmp_path):
    output = tmp_path / "imported"
    output.mkdir()
    for index, published in enumerate(("2026-10-21", "2026-10-20", "2026-01-01")):
        item = ImportedNews(
            id=f"cbr-{index}", url=f"https://cbr.ru/{index}", title=f"Новость {index}",
            published_at=published, text=_TEXT, source="Банк России", loaded_at="2026-10-21",
            sha256=str(index) * 64, status="imported",
        )
        (output / f"{item.id}.json").write_text(item.model_dump_json(), encoding="utf-8")
    result = prune_imported_news(output, retention_days=30, max_items=1)
    assert result == {"removed_by_age": 1, "removed_by_count": 1, "remaining": 1}
    assert [path.name for path in output.glob("*.json")] == ["cbr-0.json"]


def test_catalog_import_deduplicates_sources_and_persists_news_in_sqlite(monkeypatch, tmp_path):
    from app.ingest import news as news_ingest
    from app.news_store import imported_news

    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.sqlite3"))
    sources = [
        _SOURCE.model_copy(update={"min_interval_s": 0}),
        NewsSource(id="cbr_events", name="Банк России — события", url="https://www.cbr.ru/rss/eventrss", min_interval_s=0),
    ]
    monkeypatch.setattr(news_ingest, "load_news_sources", lambda: sources)
    today = date.today().strftime("%a, %d %b %Y 13:30:00 +0000")
    monkeypatch.setattr(news_ingest, "_fetch_rss", lambda url, timeout: _feed(
        _item(published=today, url="https://www.cbr.ru/press/event/?id=shared")
    ))
    first = import_news_catalog(tmp_path / "unused", max_age_days=3)
    second = import_news_catalog(tmp_path / "unused", max_age_days=3)
    stored = imported_news()
    assert first.status == "complete"
    assert sum(item["imported"] for item in first.sources) == 1
    assert sum(item["duplicates"] for item in first.sources) == 1
    assert sum(item["duplicates"] for item in second.sources) == 2
    assert len(stored) == 1
    assert not list((tmp_path / "unused").glob("*.json"))



def test_namespaced_content_encoded_is_used_as_full_rss_text(tmp_path):
    body = " ".join(["Основной текст официальной публикации содержит важные факты, параметры и пояснения."] * 18)
    payload = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<rss version='2.0' xmlns:content='http://purl.org/rss/1.0/modules/content/'>"
        "<channel><item>"
        f"<title>{_TITLE}</title><link>https://www.cbr.ru/press/event/?id=encoded</link>"
        "<guid>docid_encoded</guid><pubDate>Fri, 11 Sep 2026 13:30:00 +0300</pubDate>"
        f"<description>{html.escape(_TEXT)}</description>"
        f"<content:encoded><![CDATA[<article><p>{body}</p></article>]]></content:encoded>"
        "</item></channel></rss>"
    )
    result = import_news(
        tmp_path, source=_SOURCE, fetch=lambda: payload,
        loaded_on=date(2026, 10, 21), min_interval_s=0,
    )
    assert result.status == "imported"
    assert len(result.items[0].text) > 600
    assert "параметры и пояснения" in result.items[0].text


def test_full_article_html_is_preferred_and_navigation_is_removed(tmp_path):
    body = " ".join(["В основной части публикации описаны факты, решения и последствия для финансового рынка."] * 16)
    page = (
        "<html><body><nav>Главная Меню Контакты</nav>"
        f'<article class="article-body"><h1>{_TITLE}</h1><p>{body}</p></article>'
        "<footer>Подписаться на рассылку</footer></body></html>"
    )
    source = _SOURCE.model_copy(update={"article_mode": "html"})
    result = import_news(
        tmp_path, source=source,
        fetch=lambda: _feed(_item(text=_TEXT)),
        fetch_article=lambda _url: page,
        loaded_on=date(2026, 10, 21), min_interval_s=0,
    )
    assert result.status == "imported"
    assert len(result.items[0].text) > len(_TEXT)
    assert "Главная Меню Контакты" not in result.items[0].text
    assert "Подписаться на рассылку" not in result.items[0].text
    assert "финансового рынка" in result.items[0].text


def test_stored_article_is_enriched_when_later_fetch_has_more_text(monkeypatch, tmp_path):
    from app.news_store import imported_news, store_imported_news

    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.sqlite3"))
    base = {
        "id": "cbr-article-1", "source": "Банк России", "url": "https://www.cbr.ru/press/event/?id=enrich",
        "title": _TITLE, "published_at": "2026-10-21", "loaded_at": "2026-10-21",
        "sha256": "a" * 64, "status": "imported",
    }
    store_imported_news("cbr_press", [{**base, "text": _TEXT}])
    long_text = " ".join(["Полный текст публикации содержит важные подробности и условия принятого решения."] * 10)
    store_imported_news("cbr_press", [{**base, "id": "new-guid-id", "text": long_text, "sha256": "b" * 64}])
    stored = imported_news()
    assert len(stored) == 1
    assert stored[0]["id"] == "cbr-article-1"
    assert stored[0]["text"] == long_text
    assert stored[0]["sha256"] == "b" * 64



def test_listing_page_is_not_used_as_article_body(tmp_path):
    # Some CBR RSS entries link to a section page listing many draft regulations,
    # not to an individual publication. Keep the RSS summary instead of importing
    # every link title and site footer as if it were the article.
    links = "".join(
        f'<li><a href="/project/{index}">Проект нормативного документа Банка России номер {index} '
        + ("с подробным описанием требований к финансовым организациям " * 2)
        + "</a></li>"
        for index in range(12)
    )
    page = (
        "<html><body><main><h1>Проекты нормативных актов Банка России</h1>"
        "<h2>Публичное обсуждение</h2><ul>" + links + "</ul>"
        "<p>Страница была полезной?</p><p>Последнее обновление страницы: 23.10.2026</p>"
        "</main></body></html>"
    )
    source = _SOURCE.model_copy(update={"article_mode": "html"})
    result = import_news(
        tmp_path, source=source,
        fetch=lambda: _feed(_item(text=_TEXT)),
        fetch_article=lambda _url: page,
        loaded_on=date(2026, 10, 21), min_interval_s=0,
    )
    assert result.status == "imported"
    assert result.items[0].text == _TEXT
    assert "Проект нормативного документа" not in result.items[0].text
    assert "Последнее обновление страницы" not in result.items[0].text


def test_stored_listing_page_body_is_replaced_by_clean_rss_summary(monkeypatch, tmp_path):
    from app.news_store import imported_news, store_imported_news

    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.sqlite3"))
    polluted = "Проекты нормативных актов Банка России Публичное обсуждение " + (
        "Проект положения Банка России с перечнем документов и ссылок. " * 15
    ) + "Страница была полезной? Последнее обновление страницы: 23.10.2026"
    base = {
        "id": "cbr-listing-1", "source": "Банк России", "url": "https://www.cbr.ru/Projects/",
        "title": "Проекты нормативных актов Банка России", "published_at": "2026-10-21",
        "loaded_at": "2026-10-21", "sha256": "a" * 64, "status": "imported",
    }
    store_imported_news("cbr_news", [{**base, "text": polluted}])
    summary = "Банк России опубликовал проект нормативного акта для публичного обсуждения до 5 ноября 2026 года."
    store_imported_news("cbr_news", [{**base, "text": summary, "sha256": "b" * 64}])
    stored = imported_news()
    assert len(stored) == 1
    assert stored[0]["id"] == "cbr-listing-1"
    assert stored[0]["text"] == summary
    assert stored[0]["sha256"] == "b" * 64
