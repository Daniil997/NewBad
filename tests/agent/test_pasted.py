from datetime import date

import pytest

from app.agent.pasted import pasted_by_id, remember_pasted

_TEXT = "Совет директоров сохранил ключевую ставку на уровне 14% годовых."


def test_pasted_news_is_cleaned_and_classified(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    item = remember_pasted(
        "  <b>Ключевая ставка</b> сохранена ",
        f"<p>{_TEXT}</p>",
        "https://www.cbr.ru/press/event/?id=1",
        "Fri, 11 Sep 2026 13:30:00 +0300",
        "  ЦБ РФ  ",
    )
    assert item.status == "pasted"
    assert item.title == "Ключевая ставка сохранена"
    assert item.excerpt == _TEXT
    assert item.event_type == "monetary_policy"
    assert item.url == "https://www.cbr.ru/press/event/?id=1"
    assert item.published_at == "2026-09-11"
    assert item.source == "ЦБ РФ"
    stored = pasted_by_id(item.id)
    assert stored is not None
    assert stored.excerpt == item.excerpt
    assert stored.published_at == "2026-09-11"
    assert stored.source == "ЦБ РФ"


def test_missing_url_date_and_source_stay_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    item = remember_pasted("Ключевая ставка сохранена", _TEXT)
    assert item.url == ""
    assert item.published_at == ""
    assert item.published_at != date.today().isoformat()
    assert item.source is None
    stored = pasted_by_id(item.id)
    assert stored is not None
    assert stored.url == ""
    assert stored.published_at == ""
    assert stored.source is None


def test_empty_short_and_invalid_fields_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="заголовок"):
        remember_pasted("  ", _TEXT)
    with pytest.raises(ValueError, match="пустой"):
        remember_pasted("Ключевая ставка", " <p> </p> ")
    with pytest.raises(ValueError, match="короткий"):
        remember_pasted("Ключевая ставка", "Ставка 14%")
    with pytest.raises(ValueError, match="URL"):
        remember_pasted("Ключевая ставка", _TEXT, url="не адрес")
    with pytest.raises(ValueError, match="дата"):
        remember_pasted("Ключевая ставка", _TEXT, published_at="вчера")
