import html
from datetime import date
from urllib.parse import urlsplit

import httpx
import pytest

from app.ingest.news import NewsIngestError, NewsSource, import_news, load_news_source

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
    assert source.name == "Банк России"
    assert urlsplit(source.url).netloc == "www.cbr.ru"
    assert source.url.endswith("/rss/RssPress")


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
        _item(text="   ", guid="docid_5", url="https://www.cbr.ru/press/event/?id=5"),
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
    assert reasons == {"нет или некорректна дата", "некорректный URL", "пустой текст", "нет заголовка"}
    assert list(tmp_path.glob("cbr-*.json")) == []
    saved = (tmp_path / "import.json").read_text(encoding="utf-8")
    assert "2026-10-21" not in saved or "loaded_at" in saved
    assert "2026-09-11" not in saved


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
