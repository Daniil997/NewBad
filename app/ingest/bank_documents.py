"""Постраничное чтение PDF банка. Скан распознаётся отдельно и не записывается внутрь исходного файла."""

import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import fitz
from pydantic import BaseModel

from app.db import ROOT
from app.profile.bank_catalog import BankAppendix, load_bank_appendix

logger = logging.getLogger(__name__)

_MIN_CHARS = 40
_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


class PageFragment(BaseModel):
    doc_id: str
    title: str
    date: str
    type: str
    topic: str
    source: str
    page: int
    text: str
    kind: str
    error: str | None = None
    ocr_check: str | None = None


class IngestedDocument(BaseModel):
    id: str
    file: str
    sha256: str
    size: int
    processed: bool
    duplicate_of: str | None = None
    errors: list[str]
    pages: list[PageFragment]


def ingest_bank_documents(
    output: Path,
    catalog: Path | None = None,
    folder: Path | None = None,
    ocr_page: Callable[[bytes], str] | None = None,
    sample_pages: int = 1,
    only: set[str] | None = None,
) -> list[IngestedDocument]:
    """Читает каталог, считает сумму файла и вытаскивает текст. Ошибка страницы остаётся в результате."""
    documents = load_bank_appendix(catalog, folder)
    if only is not None:
        documents = [item for item in documents if item.id in only]
    recognize = ocr_page or _ocr_png
    checksums: dict[str, str] = {}
    result: list[IngestedDocument] = []
    for item in documents:
        size = item.path.stat().st_size
        digest = _sha256(item.path)
        earlier = checksums.get(digest)
        checksums.setdefault(digest, item.id)
        if size == 0:
            ingested = _failed(item, digest, size, ["файл пустой"])
        elif earlier:
            ingested = _failed(item, digest, size, [f"дубликат контрольной суммы: {earlier}"], earlier)
        else:
            pages, errors = _read_pdf(item, recognize, sample_pages, output)
            ingested = IngestedDocument(
                id=item.id,
                file=item.file,
                sha256=digest,
                size=size,
                processed=not errors and any(page.text.strip() for page in pages),
                errors=errors,
                pages=pages,
            )
        _write(output, ingested)
        logger.info(
            "bank-pdf id=%s pages=%s errors=%s sha256=%s",
            item.id,
            len(ingested.pages),
            len(ingested.errors),
            digest[:12],
        )
        result.append(ingested)
    return result


def _read_pdf(
    item: BankAppendix,
    ocr_page: Callable[[bytes], str],
    sample_pages: int,
    output: Path,
) -> tuple[list[PageFragment], list[str]]:
    try:
        document = fitz.open(item.path)
    except Exception as error:
        return [], [f"файл не открывается: {error.__class__.__name__}"]
    pages: list[PageFragment] = []
    errors: list[str] = []
    samples_left = sample_pages
    try:
        for index, page in enumerate(document, start=1):
            embedded = page.get_text("text").strip()
            images = len(page.get_images())
            kind = _kind(embedded, images)
            fragment = _fragment(item, index, embedded if kind == "embedded" else "", kind)
            if kind == "scanned":
                fragment, failure = _recognize(item, page, index, ocr_page, output)
                if failure:
                    errors.append(failure)
                if samples_left > 0 and fragment.error is None:
                    samples_left -= 1
                    fragment.ocr_check = _recheck_scan(page, fragment.text, ocr_page)
                    if fragment.ocr_check == "mismatch":
                        errors.append(f"страница {index}: OCR не совпал с повторным чтением")
                    elif fragment.ocr_check != "ok":
                        errors.append(f"страница {index}: повторная проверка OCR не выполнена")
            elif kind == "embedded" and samples_left > 0:
                samples_left -= 1
                fragment.ocr_check = _check_against_text(page, embedded, ocr_page)
                if fragment.ocr_check == "mismatch":
                    errors.append(f"страница {index}: OCR не совпал с текстовым слоем")
                elif fragment.ocr_check not in {"ok", "no_engine"}:
                    errors.append(f"страница {index}: проверка OCR не выполнена")
            pages.append(fragment)
    finally:
        document.close()
    return pages, errors


def _recognize(
    item: BankAppendix,
    page,
    number: int,
    ocr_page: Callable[[bytes], str],
    output: Path,
) -> tuple[PageFragment, str | None]:
    fragment = _fragment(item, number, "", "scanned")
    try:
        text = ocr_page(_png(page)).strip()
    except Exception as error:
        fragment.error = str(error) or error.__class__.__name__
        return fragment, f"страница {number}: {fragment.error}"
    fragment.kind = "ocr"
    fragment.text = text
    if not text:
        fragment.error = "OCR не вернул текст"
        return fragment, f"страница {number}: OCR не вернул текст"
    destination = output / item.id / "ocr" / f"page-{number:03d}.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return fragment, None


def _check_against_text(page, embedded: str, ocr_page: Callable[[bytes], str]) -> str:
    try:
        recognized = ocr_page(_png(page))
    except Exception as error:
        return str(error) or "no_engine"
    return "ok" if _overlap(embedded, recognized) >= 0.5 else "mismatch"


def _recheck_scan(page, saved: str, ocr_page: Callable[[bytes], str]) -> str:
    try:
        again = ocr_page(_png(page))
    except Exception as error:
        return str(error) or "no_engine"
    return "ok" if _overlap(saved, again) >= 0.5 else "mismatch"


def _kind(text: str, images: int) -> str:
    if len(text) >= _MIN_CHARS:
        return "embedded"
    if images:
        return "scanned"
    if text:
        return "sparse"
    return "empty"


def _fragment(item: BankAppendix, page: int, text: str, kind: str) -> PageFragment:
    return PageFragment(
        doc_id=item.id,
        title=item.title,
        date=item.date,
        type=item.type,
        topic=item.topic,
        source=item.source,
        page=page,
        text=text,
        kind=kind,
    )


def _failed(
    item: BankAppendix,
    digest: str,
    size: int,
    errors: list[str],
    duplicate_of: str | None = None,
) -> IngestedDocument:
    return IngestedDocument(
        id=item.id,
        file=item.file,
        sha256=digest,
        size=size,
        processed=False,
        duplicate_of=duplicate_of,
        errors=errors,
        pages=[],
    )


def _write(output: Path, document: IngestedDocument) -> None:
    folder = output / document.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pages.json").write_text(document.model_dump_json(indent=2), encoding="utf-8")


def _png(page) -> bytes:
    return page.get_pixmap(dpi=120).tobytes("png")


def _ocr_png(png: bytes) -> str:
    executable = shutil.which("tesseract")
    if executable is None and _TESSERACT.is_file():
        executable = str(_TESSERACT)
    if executable is None:
        raise RuntimeError("tesseract не найден")
    tessdata = ROOT / "data" / "tessdata"
    if not (tessdata / "rus.traineddata").is_file():
        raise RuntimeError("нет файла rus.traineddata")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(png)
        image = handle.name
    try:
        completed = subprocess.run(
            [executable, image, "stdout", "-l", "rus", "--tessdata-dir", str(tessdata), "--psm", "6"],
            capture_output=True,
            check=False,
            timeout=120,
        )
    finally:
        Path(image).unlink(missing_ok=True)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "tesseract завершился с ошибкой")
    return completed.stdout.decode("utf-8", errors="replace")


def _overlap(left: str, right: str) -> float:
    first = set(_TOKEN.findall(left.lower().replace("ё", "е")))
    second = set(_TOKEN.findall(right.lower().replace("ё", "е")))
    if not first or not second:
        return 0.0
    return len(first & second) / len(first)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
