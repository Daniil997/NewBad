import json
from datetime import date

import pytest

from app.corpus.search import load_news
from app.ingest.news import NewsSource, import_news
from app.news_store import imported_news, store_imported_news
from app.news_topics import news_topic
from app.personal_feed import article_hash
from app.scoring.feed import corpus_version
from app.user_store import connect_app, init_app_db


@pytest.mark.parametrize(("title", "body", "expected"), [
    ("Глава «Новатэка» призвал ЕС пересмотреть планы по отказу от российского СПГ",
     "Перенаправление поставок на другие рынки вызовет рост мировых цен.",
     ("commodities_energy", "Сырьё и энергоносители")),
    ("Ставка RUONIA", "Вся информация по процентной ставке RUONIA опубликована Банком России.",
     ("money_market", "Денежный рынок")),
    ("Инфляция замедлилась", "В обзоре также рассматриваются доллар, золото и рынок труда.",
     ("inflation_macro", "Инфляция")),
    ("Рынок вырос на фоне новостей", "В сообщении нет конкретной темы.",
     ("unknown", "Новости бизнеса")),
])
def test_public_topic_uses_headline_evidence_without_guessing(title, body, expected):
    assert news_topic(title, body) == expected


def test_rss_topic_is_assigned_and_written_during_parsing(tmp_path):
    source = NewsSource(id="test", name="Тестовый источник", url="https://example.com/rss")
    rss = """<rss><channel><item><title>Ставка RUONIA</title>
        <link>https://example.com/ruonia</link><pubDate>Fri, 23 Oct 2026 12:00:00 +0300</pubDate>
        <description>Вся информация по процентной ставке RUONIA опубликована Банком России.</description>
        </item></channel></rss>"""
    result = import_news(tmp_path, source=source, fetch=lambda: rss,
                         loaded_on=date(2026, 10, 23), min_interval_s=0)
    item = result.items[0]
    assert (item.event_type, item.event_title) == ("money_market", "Денежный рынок")
    saved = json.loads((tmp_path / f"{item.id}.json").read_text(encoding="utf-8"))
    assert saved["event_title"] == "Денежный рынок"
    assert store_imported_news(source.id, [item.model_dump()]) == 1
    assert imported_news()[0]["event_title"] == saved["event_title"]


def _article():
    return {"id": "shared", "title": "Ставка RUONIA", "source": "Источник",
            "url": "https://example.com/shared", "published_at": "2026-10-23",
            "text": "Вся информация по процентной ставке RUONIA опубликована Банком России.",
            "sha256": "a" * 64, "loaded_at": "2026-10-23"}


def _unexpected_classification(*args, **kwargs):
    raise AssertionError("Reading a feed must reuse the stored topic")


def test_repeated_reads_reuse_shared_metadata_without_classification(monkeypatch, tmp_path):
    store_imported_news("test", [_article()])
    monkeypatch.setattr("app.corpus.search.news_topic", _unexpected_classification)
    monkeypatch.setattr("app.news_store.news_topic", _unexpected_classification)
    monkeypatch.setattr("app.news_topics.news_topic", _unexpected_classification)
    first = load_news(tmp_path)
    second = load_news(tmp_path)
    assert len(first) == len(second) == 1
    assert first[0].event_title == second[0].event_title == "Денежный рынок"


def test_duplicate_and_enriched_content_keep_the_original_topic():
    original = _article()
    store_imported_news("test", [original])
    store_imported_news("test", [{**original, "id": "changed-guid",
                                 "text": original["text"] + " Дополнительные подробности о ключевой ставке.",
                                 "sha256": "b" * 64,
                                 "event_type": "monetary_policy", "event_title": "Новая категория"}])
    saved = imported_news()
    assert len(saved) == 1
    assert saved[0]["id"] == "shared"
    assert saved[0]["event_title"] == "Денежный рынок"
    assert saved[0]["sha256"] == "b" * 64


def test_topic_changes_invalidate_ranking_but_label_changes_do_not_invalidate_analysis(monkeypatch, tmp_path):
    store_imported_news("test", [_article()])
    item = load_news(tmp_path)[0]
    monkeypatch.setattr("app.scoring.feed.load_news", lambda: [item])
    monkeypatch.setattr("app.scoring.feed.load_bank_documents", lambda: [])
    version = corpus_version()
    digest = article_hash(item)
    item.event_type = "unknown"
    assert corpus_version() != version
    assert article_hash(item) != digest
    digest = article_hash(item)
    item.event_title = "Другая подпись"
    assert article_hash(item) == digest


def test_v10_migration_backfills_once_without_changing_article(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "legacy.sqlite3"))
    with connect_app() as connection:
        connection.executescript("""
            CREATE TABLE news_articles (
                id TEXT PRIMARY KEY, source_id TEXT, source TEXT, url TEXT, title TEXT,
                published_at TEXT, text TEXT, loaded_at TEXT, sha256 TEXT, created_at TEXT
            );
            PRAGMA user_version = 10;
        """)
        row = _article()
        connection.execute("INSERT INTO news_articles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (row["id"], "test", row["source"], row["url"], row["title"], row["published_at"],
                            row["text"], row["loaded_at"], row["sha256"], "2026-10-23"))
    init_app_db()
    monkeypatch.setattr("app.news_topics.news_topic", _unexpected_classification)
    init_app_db()
    saved = imported_news()[0]
    assert saved["event_title"] == "Денежный рынок"
    assert {key: saved[key] for key in row} == row
    with connect_app() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
