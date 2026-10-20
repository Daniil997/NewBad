import os
from pathlib import Path

import duckdb

from app.db import ROOT, data_dir

COMPANY_COLUMNS = {"SYNTHETIC_U"}
PAYMENT_COLUMNS = {"PAYER", "BENEFICIARY"}
CASE_COMPANIES = "df_Uid_data_hack.csv"
CASE_PAYMENTS = "df_Uid_payment_hack.csv"
CASE_PERSONS = "df_Fid_Uid_link_hack.csv"


def using_case_extract() -> bool:
    source = os.environ.get("PROFILE_SOURCE", "auto")
    if source == "demo":
        return False
    return case_dir() is not None and (case_dir() / CASE_COMPANIES).is_file()


def read_csv(path: Path) -> list[dict[str, str]]:
    connection = duckdb.connect()
    try:
        cursor = connection.execute(
            "SELECT * FROM read_csv_auto(?, header=true, all_varchar=true)",
            [str(path)],
        )
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


def companies_csv() -> Path:
    if os.environ.get("PROFILE_SOURCE", "auto") == "demo":
        return ROOT / "data" / "demo" / "companies.csv"
    case = _case_file(CASE_COMPANIES)
    if case is not None:
        return case
    found = _csv_with_columns(data_dir() / "raw", COMPANY_COLUMNS)
    if found is not None:
        return found
    return ROOT / "data" / "demo" / "companies.csv"


def payments_csv() -> Path | None:
    if os.environ.get("PROFILE_SOURCE", "auto") == "demo":
        demo = ROOT / "data" / "demo" / "payments.csv"
        return demo if demo.is_file() else None
    case = _case_file(CASE_PAYMENTS)
    if case is not None:
        return case
    found = _csv_with_columns(data_dir() / "raw", PAYMENT_COLUMNS)
    if found is not None:
        return found
    demo = ROOT / "data" / "demo" / "payments.csv"
    if demo.is_file():
        return demo
    return None


def persons_csv() -> Path | None:
    if os.environ.get("PROFILE_SOURCE", "auto") == "demo":
        return None
    return _case_file(CASE_PERSONS)


def case_dir() -> Path | None:
    raw = os.environ.get("CASE_DATA_DIR")
    if raw:
        path = Path(raw)
        return path if path.is_dir() else None
    candidate = ROOT.parent / "Doc" / "Данные данные"
    return candidate if candidate.is_dir() else None


def _case_file(name: str) -> Path | None:
    folder = case_dir()
    if folder is None:
        return None
    path = folder / name
    return path if path.is_file() else None


def _csv_with_columns(directory: Path, required: set[str]) -> Path | None:
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.csv")):
        header = path.open(encoding="utf-8").readline()
        columns = {part.strip().lstrip("\ufeff") for part in _header_cells(header)}
        if required <= columns:
            return path
    return None


def _header_cells(header: str) -> list[str]:
    line = header.splitlines()[0] if header else ""
    separator = ";" if line.count(";") > line.count(",") else ","
    return line.split(separator)
