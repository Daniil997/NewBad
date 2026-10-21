"""Каталог PDF банка. Наличие записи в YAML не означает, что документ обработан."""

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from app.db import ROOT

_REQUIRED = ("id", "file", "title", "date", "type", "topic", "source")
_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


class BankCatalogError(Exception):
    """missing — нет папки или файла, schema — нет обязательного поля или тип неизвестен."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


class BankAppendix(BaseModel):
    id: str
    file: str
    title: str
    date: str
    type: str
    topic: str
    source: str
    path: Path
    processed: bool = False


def load_bank_appendix(catalog: Path | None = None, folder: Path | None = None) -> list[BankAppendix]:
    payload = yaml.safe_load((catalog or _catalog_path()).read_text(encoding="utf-8"))
    rows = payload.get("documents") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise BankCatalogError("schema", "В каталоге банка нет списка documents")
    known_types = set(_taxonomy_types())
    pdf_dir = folder if folder is not None else _pdf_dir()
    items: list[BankAppendix] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise BankCatalogError("schema", "Запись каталога банка должна быть объектом")
        _require(row)
        doc_id = str(row["id"]).strip()
        if not doc_id or doc_id in seen:
            raise BankCatalogError("schema", f"Повторяется или пуст id документа: {doc_id or 'пусто'}")
        seen.add(doc_id)
        doc_type = str(row["type"]).strip()
        if doc_type not in known_types:
            raise BankCatalogError("schema", f"Неизвестный тип документа {doc_id}: {doc_type}")
        date = str(row["date"]).strip()
        if date != "unknown" and _DATE.fullmatch(date) is None:
            raise BankCatalogError("schema", f"Дата документа {doc_id} должна быть ГГГГ-ММ, ГГГГ-ММ-ДД или unknown")
        source = str(row["source"]).strip()
        if source != "bank_analytics":
            raise BankCatalogError("schema", f"Источник документа {doc_id} должен быть bank_analytics")
        name = str(row["file"]).strip()
        path = pdf_dir / name
        if not path.is_file():
            raise BankCatalogError("missing", f"Файл банка не найден: {pdf_dir.name}/{name}")
        items.append(
            BankAppendix(
                id=doc_id,
                file=name,
                title=str(row["title"]).strip(),
                date=date,
                type=doc_type,
                topic=str(row["topic"]).strip(),
                source=source,
                path=path,
                processed=False,
            )
        )
    return items


def _catalog_path() -> Path:
    return ROOT / "config" / "bank_appendix.yaml"


def _pdf_dir() -> Path:
    raw = os.environ.get("BANK_PDF_DIR")
    if raw:
        path = Path(raw)
        if not path.is_dir():
            raise BankCatalogError("missing", f"Папка PDF не найдена: {path.name or raw}")
        return path
    legacy = ROOT.parent / "Doc" / "Данные данные"
    if legacy.is_dir():
        return legacy
    raise BankCatalogError("missing", "Папка PDF не найдена: Данные данные")


def _taxonomy_types() -> list[str]:
    payload = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))
    return list(payload["event_types"])


def _require(row: dict) -> None:
    missing = [name for name in _REQUIRED if not str(row.get(name) or "").strip()]
    if missing:
        label = str(row.get("id") or row.get("file") or "запись")
        raise BankCatalogError("schema", f"У документа {label} нет полей: {', '.join(missing)}")
