import hashlib
import json
import re

import fitz
import pytest
import yaml

from app.agent.tools import bank_analytics
from app.corpus.search import CorpusError, _COMMON, _STOP, _tokenize, load_bank_documents
from app.db import ROOT
from app.ingest.bank_documents import IngestedDocument, ingest_bank_documents
from app.profile.bank_catalog import BankCatalogError, load_bank_appendix

_CACHE: list[IngestedDocument] | None = None


def _ready() -> bool:
    if not (ROOT / "data" / "tessdata" / "rus.traineddata").is_file():
        return False
    try:
        return bool(load_bank_appendix())
    except BankCatalogError:
        return False


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _specific(text: str) -> set[str]:
    return {token for token in _tokenize(text, stem=True) if token not in _STOP and token not in _COMMON}


def _ingested() -> list[IngestedDocument]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not _ready():
        pytest.skip("Нет PDF банка или rus.traineddata")
    catalog = load_bank_appendix()
    output = ROOT / "data" / "bank" / "extracted"
    stale: list[str] = []
    for item in catalog:
        path = output / item.id / "pages.json"
        digest = hashlib.sha256(item.path.read_bytes()).hexdigest()
        if not path.is_file():
            stale.append(item.id)
            continue
        saved = IngestedDocument.model_validate_json(path.read_text(encoding="utf-8"))
        if saved.sha256 != digest:
            stale.append(item.id)
    if stale:
        ingest_bank_documents(output, only=set(stale))
    loaded: list[IngestedDocument] = []
    for item in catalog:
        path = output / item.id / "pages.json"
        loaded.append(IngestedDocument.model_validate_json(path.read_text(encoding="utf-8")))
    _CACHE = loaded
    return loaded


def _by_page(documents: list[IngestedDocument]) -> dict[tuple[str, int], str]:
    pages: dict[tuple[str, int], str] = {}
    for document in documents:
        for page in document.pages:
            if page.kind == "ocr" and page.text.strip():
                pages[(document.id, page.page)] = _normalize(page.text)
    return pages


def test_each_catalog_pdf_is_processed_or_has_a_visible_error():
    catalog = {item.id: item for item in load_bank_appendix()} if _ready() else {}
    if not catalog:
        pytest.skip("Нет PDF банка или rus.traineddata")
    documents = _ingested()
    assert {item.id for item in documents} == set(catalog)
    output = ROOT / "data" / "bank" / "extracted"
    for document in documents:
        source = catalog[document.id]
        assert hashlib.sha256(source.path.read_bytes()).hexdigest() == document.sha256
        assert document.processed or document.errors
        assert all(error.strip() for error in document.errors)
        assert [page.page for page in document.pages] == list(range(1, len(document.pages) + 1))
        with fitz.open(source.path) as pdf:
            if document.pages:
                assert len(document.pages) == pdf.page_count
        if document.processed:
            recognized = [page for page in document.pages if page.kind == "ocr"]
            assert recognized
            for page in recognized:
                assert page.doc_id == document.id
                assert page.date == source.date
                sidecar = output / document.id / "ocr" / f"page-{page.page:03d}.txt"
                assert sidecar.is_file()
                assert _normalize(sidecar.read_text(encoding="utf-8")) == _normalize(page.text)
        else:
            saved = json.loads((output / document.id / "pages.json").read_text(encoding="utf-8"))
            assert saved["errors"]


def test_every_quote_is_on_the_cited_page():
    documents = _ingested()
    extracted = _by_page(documents)
    cited = bank_analytics("инфляция")
    assert cited["status"] in {"found", "conflict"}
    hit = cited["hits"][0]
    assert hit["page"] is not None
    assert hit["source_type"] == "bank_analytics"
    assert hit["quote"] in extracted[(hit["id"], hit["page"])]
    for item in load_bank_documents():
        if item.theses or (item.doc_id, item.page) not in extracted:
            continue
        assert item.quote in extracted[(item.doc_id, item.page)]
        assert len(item.quote.split()) <= 25


def test_search_does_not_treat_an_unrelated_page_as_confirmation():
    _ingested()
    assert bank_analytics("данные")["status"] == "none"
    assert bank_analytics("банк")["hits"] == []
    result = bank_analytics("ключевая ставка")
    assert result["hits"]
    assert result["hits"][0]["id"] == "key_rate"
    assert result["hits"][0]["page"] == 1
    if result["status"] == "conflict":
        assert result["message"]
        assert "2026-09-11" in result["message"]
    else:
        assert result["status"] == "found"
    wanted = _specific("ключевая ставка")
    extracted = _by_page(_ingested())
    for hit in result["hits"]:
        text = extracted.get((hit["id"], hit["page"]), "")
        if hit["quote"] not in text:
            passport = next(
                item.text
                for item in load_bank_documents()
                if item.doc_id == hit["id"] and item.page == hit["page"] and hit["quote"] in item.text
            )
            text = passport
        assert hit["quote"] in text
        assert wanted & _specific(text)


def test_empty_corpus_and_broken_file_stay_distinct(tmp_path):
    assert bank_analytics("инфляция", folder=tmp_path) == {
        "status": "none",
        "message": "Нет релевантных материалов банка по этому запросу.",
        "hits": [],
    }
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(CorpusError) as error:
        bank_analytics("инфляция", folder=tmp_path)
    assert error.value.kind == "load"


def test_failed_ocr_is_saved_for_the_operator(tmp_path):
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 30, 30), 0)
    pixmap.clear_with(255)
    image = tmp_path / "pixel.png"
    pixmap.save(image)
    document = fitz.open()
    page = document.new_page()
    page.insert_image(page.rect, filename=str(image))
    document.save(pdf_dir / "rate.pdf")
    document.close()
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        yaml.safe_dump(
            {
                "documents": [
                    {
                        "id": "key_rate",
                        "file": "rate.pdf",
                        "title": "Ключевая ставка",
                        "date": "2026-09-11",
                        "type": "monetary_policy",
                        "topic": "key_rate",
                        "source": "bank_analytics",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    def broken(_png: bytes) -> str:
        raise RuntimeError("tesseract не найден")

    result = ingest_bank_documents(tmp_path / "out", catalog, pdf_dir, broken)
    assert result[0].processed is False
    assert result[0].pages[0].page == 1
    assert "tesseract не найден" in result[0].errors[0]
    saved = json.loads((tmp_path / "out" / "key_rate" / "pages.json").read_text(encoding="utf-8"))
    assert saved["processed"] is False
    assert "tesseract не найден" in saved["errors"][0]
    assert saved["pages"][0]["error"] == "tesseract не найден"
