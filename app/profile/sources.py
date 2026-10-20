from pathlib import Path

import duckdb

from app.db import ROOT, data_dir

COMPANY_COLUMNS = {"SYNTHETIC_U"}
PAYMENT_COLUMNS = {"PAYER", "BENEFICIARY"}


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
    found = _csv_with_columns(data_dir() / "raw", COMPANY_COLUMNS)
    if found is not None:
        return found
    return ROOT / "data" / "demo" / "companies.csv"


def payments_csv() -> Path | None:
    found = _csv_with_columns(data_dir() / "raw", PAYMENT_COLUMNS)
    if found is not None:
        return found
    demo = ROOT / "data" / "demo" / "payments.csv"
    if demo.is_file():
        return demo
    return None


def _csv_with_columns(directory: Path, required: set[str]) -> Path | None:
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.csv")):
        header = path.open(encoding="utf-8").readline()
        columns = {part.strip().lstrip("\ufeff") for part in header.split(",")}
        if required <= columns:
            return path
    return None
