import json
from datetime import date

import pytest

from app.corpus.search import CorpusError, load_news, search_news

_TODAY = date(2026, 10, 21)


def _write(folder, name: str, payload: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _article(news_id: str, title: str, excerpt: str, published: str, url: str) -> dict:
    return {
        "id": news_id,
        "url": url,
        "title": title,
        "published_at": published,
        "excerpt": excerpt,
    }


def test_file_and_sqlite_import_formats_are_compatible(tmp_path):
    _write(
        tmp_path,
        "rate.json",
        _article(
            "rate",
            "Ключевая ставка сохранена",
            "Регулятор оставил ставку без изменения.",
            "2026-09-11",
            "https://www.cbr.ru/press/rate",
        ),
    )
    _write(
        tmp_path / "imported",
        "cbr-gold.json",
        {
            "id": "cbr-gold",
            "url": "https://www.cbr.ru/press/gold",
            "title": "Обзор рынка",
            "published_at": "2026-09-02",
            "text": "Цена золота обновила максимум месяца.",
            "source": "Банк России",
            "loaded_at": "2026-10-21",
            "sha256": "a" * 64,
            "status": "imported",
        },
    )
    _write(tmp_path / "imported", "import.json", {"status": "imported", "items": []})
    news = {item.id: item for item in load_news(tmp_path, _TODAY)}
    assert set(news) == {"rate", "cbr-gold"}
    assert news["rate"].status == "imported"
    assert news["rate"].excerpt.startswith("Регулятор")
    imported = news["cbr-gold"]
    assert imported.excerpt == "Цена золота обновила максимум месяца."
    assert imported.source == "Банк России"
    assert imported.loaded_at == "2026-10-21"
    assert imported.status == "imported"
    assert imported.stale is False


def test_search_uses_title_and_body_and_keeps_url(tmp_path):
    _write(
        tmp_path,
        "title.json",
        _article(
            "title-only",
            "Редкийзаголовок без деталей",
            "Короткое сообщение без искомого слова.",
            "2026-09-01",
            "https://www.cbr.ru/press/title",
        ),
    )
    _write(
        tmp_path,
        "body.json",
        _article(
            "body-only",
            "Обычное сообщение",
            "В тексте упоминается редкийабзац и больше ничего.",
            "2026-09-02",
            "https://www.cbr.ru/press/body",
        ),
    )
    by_title = search_news("редкийзаголовок", folder=tmp_path, today=_TODAY)
    by_body = search_news("редкийабзац", folder=tmp_path, today=_TODAY)
    assert [item.id for item in by_title] == ["title-only"]
    assert [item.id for item in by_body] == ["body-only"]
    assert by_title[0].url == "https://www.cbr.ru/press/title"
    assert by_title[0].published_at == "2026-09-01"
    assert by_body[0].url == "https://www.cbr.ru/press/body"


def test_stale_duplicate_and_incomplete_news(tmp_path):
    _write(
        tmp_path,
        "fresh.json",
        _article(
            "fresh",
            "Свежая заметка про металл",
            "Свежий обзор рынка металла.",
            "2026-09-11",
            "https://www.cbr.ru/press/fresh",
        ),
    )
    _write(
        tmp_path,
        "old.json",
        _article(
            "old",
            "Старая заметка про металл металл металл металл",
            "Старый обзор рынка металла металл металл металл.",
            "2024-01-15",
            "https://www.cbr.ru/press/old",
        ),
    )
    _write(
        tmp_path / "imported",
        "copy.json",
        {
            "id": "copy",
            "url": "https://www.cbr.ru/press/fresh",
            "title": "Повтор",
            "published_at": "2026-09-11",
            "text": "Тот же адрес.",
            "source": "Банк России",
            "loaded_at": "2026-10-21",
            "sha256": "b" * 64,
            "status": "imported",
        },
    )
    _write(
        tmp_path / "imported",
        "marked.json",
        {
            "id": "marked",
            "url": "https://www.cbr.ru/press/marked",
            "title": "Отмеченный дубль про металл",
            "published_at": "2026-09-11",
            "text": "Этот текст не индексируется.",
            "source": "Банк России",
            "loaded_at": "2026-10-21",
            "sha256": "c" * 64,
            "status": "duplicate",
        },
    )
    _write(
        tmp_path,
        "partial.json",
        {"id": "partial", "title": "Без даты и адреса", "excerpt": "Текст есть, даты нет."},
    )
    news = {item.id: item for item in load_news(tmp_path, _TODAY)}
    assert set(news) == {"fresh", "old"}
    assert news["old"].stale is True
    assert news["fresh"].stale is False
    hits = search_news("металл", folder=tmp_path, today=_TODAY)
    assert [item.id for item in hits] == ["fresh", "old"]
    assert hits[0].stale is False
    assert hits[1].stale is True
    assert hits[0].url == "https://www.cbr.ru/press/fresh"

    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(CorpusError) as error:
        load_news(tmp_path, _TODAY)
    assert error.value.kind == "load"
