import os
from pathlib import Path

from app.db import ROOT


def bank_pdf_dir() -> Path | None:
    """Папка с PDF аналитики банка: BANK_PDF_DIR, новая DATA/bank_analytics, затем прежний путь."""
    raw = os.environ.get("BANK_PDF_DIR")
    if raw:
        path = Path(raw)
        return path if path.is_dir() else None
    data = ROOT.parent / "DATA" / "bank_analytics"
    if data.is_dir():
        return data
    legacy = ROOT.parent / "Doc" / "Данные данные"
    return legacy if legacy.is_dir() else None
