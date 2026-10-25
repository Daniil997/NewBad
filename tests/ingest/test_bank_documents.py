import hashlib
from pathlib import Path

import fitz
import pytest
import yaml

from app.db import ROOT
from app.ingest.bank_documents import ingest_bank_documents

_TEXT = "Stavka sohranena na urovne 14 percent today"


def _catalog(folder, rows):
    folder.mkdir(exist_ok=True)
    path = folder / "catalog.yaml"
    path.write_text(yaml.safe_dump({"documents": rows}, allow_unicode=True), encoding="utf-8")
    return path


def _row(doc_id: str, file_name: str) -> dict:
    return {
        "id": doc_id,
        "file": file_name,
        "title": "Ключевая ставка",
        "date": "2026-09-11",
        "type": "monetary_policy",
        "topic": "key_rate",
        "source": "bank_analytics",
    }


def _save(document, path) -> None:
    document.save(path)
    document.close()


def _text_pdf(path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), _TEXT)
    document.new_page()
    short = document.new_page()
    short.insert_text((72, 72), "1")
    _save(document, path)


def _scan_pdf(path, image: Path) -> None:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 30, 30), 0)
    pixmap.clear_with(255)
    pixmap.save(image)
    document = fitz.open()
    page = document.new_page()
    page.insert_image(page.rect, filename=str(image))
    _save(document, path)


def test_pages_keep_metadata_and_flag_empty(tmp_path):
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    _text_pdf(pdf_dir / "rate.pdf")
    catalog = _catalog(tmp_path, [_row("key_rate", "rate.pdf")])
    calls = {"n": 0}

    def fake(_png: bytes) -> str:
        calls["n"] += 1
        return _TEXT

    result = ingest_bank_documents(tmp_path / "out", catalog, pdf_dir, fake)
    document = result[0]
    assert document.processed is True
    assert document.size > 0
    assert len(document.sha256) == 64
    kinds = {page.page: page.kind for page in document.pages}
    assert kinds == {1: "embedded", 2: "empty", 3: "sparse"}
    first = document.pages[0]
    assert first.text == _TEXT
    assert first.doc_id == "key_rate"
    assert first.date == "2026-09-11"
    assert first.source == "bank_analytics"
    assert first.ocr_check == "ok"
    assert calls["n"] == 1
    assert (tmp_path / "out" / "key_rate" / "pages.json").is_file()


def test_scan_ocr_is_stored_apart_from_the_pdf(tmp_path):
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    _scan_pdf(pdf_dir / "rate.pdf", tmp_path / "pixel.png")
    before = hashlib.sha256((pdf_dir / "rate.pdf").read_bytes()).hexdigest()
    catalog = _catalog(tmp_path, [_row("key_rate", "rate.pdf")])

    def fake(_png: bytes) -> str:
        return "ставка сохранена"

    result = ingest_bank_documents(tmp_path / "out", catalog, pdf_dir, fake)
    assert hashlib.sha256((pdf_dir / "rate.pdf").read_bytes()).hexdigest() == before
    page = result[0].pages[0]
    assert page.kind == "ocr"
    assert page.ocr_check == "ok"
    saved = tmp_path / "out" / "key_rate" / "ocr" / "page-001.txt"
    assert saved.read_text(encoding="utf-8") == "ставка сохранена"
    assert result[0].processed is True


def test_ocr_failure_duplicate_and_mismatch_are_visible(tmp_path):
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    _scan_pdf(pdf_dir / "rate.pdf", tmp_path / "pixel.png")
    (pdf_dir / "copy.pdf").write_bytes((pdf_dir / "rate.pdf").read_bytes())
    (pdf_dir / "empty.pdf").write_bytes(b"")
    (pdf_dir / "broken.pdf").write_bytes(b"not-a-pdf")
    rows = [
        _row("key_rate", "rate.pdf"),
        _row("copy", "copy.pdf"),
        _row("empty", "empty.pdf"),
        _row("broken", "broken.pdf"),
    ]
    catalog = _catalog(tmp_path, rows)

    def broken(_png: bytes) -> str:
        raise RuntimeError("tesseract не найден")

    result = {item.id: item for item in ingest_bank_documents(tmp_path / "out", catalog, pdf_dir, broken)}
    assert result["key_rate"].processed is False
    assert result["key_rate"].pages[0].error == "tesseract не найден"
    assert result["copy"].duplicate_of == "key_rate"
    assert result["copy"].errors
    assert result["empty"].errors == ["файл пустой"]
    assert result["broken"].errors
    assert all(item.processed is False for item in result.values())

    text_dir = tmp_path / "text"
    text_dir.mkdir()
    _text_pdf(text_dir / "rate.pdf")
    text_catalog = _catalog(tmp_path / "text-meta", [_row("key_rate", "rate.pdf")])

    def other(_png: bytes) -> str:
        return "pogoda dozhd veter"

    mismatched = ingest_bank_documents(tmp_path / "text-out", text_catalog, text_dir, other)
    assert mismatched[0].pages[0].ocr_check == "mismatch"
    assert mismatched[0].processed is False


def test_inflation_scan_ocr_matches_a_second_read(tmp_path):
    from app.profile.sources import bank_pdf_dir

    pdf_dir = bank_pdf_dir()
    pdf = (pdf_dir / "Inflation_tracker_02.pdf") if pdf_dir is not None else ROOT / "missing.pdf"
    tessdata = ROOT / "data" / "tessdata" / "rus.traineddata"
    if not pdf.is_file() or not tessdata.is_file():
        pytest.skip("Нет скана инфляции или rus.traineddata")
    output = tmp_path / "extracted"
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    result = ingest_bank_documents(output, only={"inflation"})
    document = result[0]
    assert document.id == "inflation"
    assert document.pages[0].kind == "ocr"
    assert document.pages[0].page == 1
    assert document.pages[0].ocr_check == "ok"
    assert "2026" in document.pages[0].text
    assert (output / "inflation" / "ocr" / "page-001.txt").is_file()
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before
