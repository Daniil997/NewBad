import pytest

from app.corpus.search import CorpusError, load_bank_documents, search_bank_analytics
from app.ingest.bank_documents import IngestedDocument, PageFragment

_GOOD = (
    "Сохранение ключевой ставки удерживает стоимость заёмных денег "
    "и не снижает платежи по кредиту компании в этом квартале"
)
_COMMON = "Данные банка за этот год показывают рост и динамику рынка без отдельного события для компании"


def _page(doc_id: str, page: int, text: str, kind: str, error: str | None = None) -> PageFragment:
    return PageFragment(
        doc_id=doc_id,
        title="Проверка поиска",
        date="2026-09-11",
        type="monetary_policy",
        topic="key_rate",
        source="bank_analytics",
        page=page,
        text=text,
        kind=kind,
        error=error,
    )


def _write(
    folder,
    doc_id: str,
    pages: list[PageFragment],
    *,
    processed: bool = True,
    duplicate_of: str | None = None,
) -> None:
    document = IngestedDocument(
        id=doc_id,
        file=f"{doc_id}.pdf",
        sha256="a" * 64,
        size=10,
        processed=processed,
        duplicate_of=duplicate_of,
        errors=[] if processed else ["файл пустой"],
        pages=pages,
    )
    path = folder / "extracted" / doc_id / "pages.json"
    path.parent.mkdir(parents=True)
    path.write_text(document.model_dump_json(), encoding="utf-8")


def test_search_skips_empty_failed_and_duplicate_pages(tmp_path):
    _write(
        tmp_path,
        "rate",
        [
            _page("rate", 1, "", "empty"),
            _page("rate", 2, "1", "sparse"),
            _page("rate", 3, _GOOD, "scanned"),
            _page("rate", 4, _GOOD, "ocr", error="OCR не вернул текст"),
            _page("rate", 5, _GOOD, "ocr"),
            _page("rate", 6, _COMMON, "embedded"),
        ],
    )
    _write(tmp_path, "copy", [_page("copy", 1, _GOOD, "ocr")], duplicate_of="rate")
    _write(tmp_path, "broken", [_page("broken", 1, _GOOD, "ocr")], processed=False)

    documents = load_bank_documents(tmp_path)
    assert [(item.doc_id, item.page) for item in documents] == [("rate", 5), ("rate", 6)]
    kept = documents[0]
    assert kept.title == "Проверка поиска"
    assert kept.published_at == "2026-09-11"
    assert kept.source_type == "bank_analytics"
    assert kept.quote in kept.text
    assert len(kept.quote.split()) <= 25


def test_search_returns_page_and_keeps_word_forms(tmp_path):
    _write(
        tmp_path,
        "rate",
        [_page("rate", 5, _GOOD, "ocr"), _page("rate", 6, _COMMON, "embedded")],
    )
    hits = search_bank_analytics("ключевую ставке", folder=tmp_path)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.id == "rate"
    assert hit.title == "Проверка поиска"
    assert hit.published_at == "2026-09-11"
    assert hit.page == 5
    assert hit.source_type == "bank_analytics"
    assert hit.excerpt in _GOOD
    assert hit.strength == "strong"

    narrow = search_bank_analytics("ставке", folder=tmp_path)
    assert [item.id for item in narrow] == ["rate"]
    assert narrow[0].strength == "narrow"
    assert search_bank_analytics("данные банка", folder=tmp_path) == []


def test_real_corpus_finds_inflation_and_drops_a_common_word():
    documents = load_bank_documents()
    pages = [item for item in documents if item.doc_id == "inflation"]
    assert pages
    assert all(item.source_type == "bank_analytics" and item.text.strip() for item in pages)

    hits = search_bank_analytics("недельная инфляция")
    assert hits
    assert hits[0].id == "inflation"
    assert hits[0].strength == "strong"
    assert hits[0].page is not None
    source = next(item for item in pages if item.page == hits[0].page)
    assert hits[0].excerpt in source.text
    assert hits[0].published_at == "2026-09-02"

    forms = search_bank_analytics("инфляции")
    assert forms
    assert forms[0].id == "inflation"
    assert forms[0].strength == "narrow"
    assert search_bank_analytics("данные") == []
    assert search_bank_analytics("ключевая ставка")[0].id == "key_rate"


def test_empty_corpus_is_not_a_load_error(tmp_path, monkeypatch):
    assert search_bank_analytics("инфляция", folder=tmp_path) == []
    assert search_bank_analytics("инфляция", folder=tmp_path / "missing") == []

    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(CorpusError) as load_error:
        search_bank_analytics("инфляция", folder=tmp_path)
    assert load_error.value.kind == "load"

    class BrokenIndex:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("индекс")

    monkeypatch.setattr("app.corpus.search.BM25Okapi", BrokenIndex)
    with pytest.raises(CorpusError) as index_error:
        search_bank_analytics("ключевая ставка")
    assert index_error.value.kind == "index"
