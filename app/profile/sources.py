import csv
import logging
import os
from pathlib import Path

from app.db import ROOT

logger = logging.getLogger(__name__)

COMPANY_COLUMNS = {
    "SYNTHETIC_U",
    "ORG_TYPE",
    "OKFS_NAME",
    "COMPANY_AGE_YEARS_GRADATION",
    "ECO_MARK_LABEL",
    "MAINOKVED_CODE",
    "MAINOKVED_NAME",
    "NEGATIVEMARK",
    "REVENUE_COMPANY",
    "GROUP_ID",
    "AMOUNT_WORKERS_FNS_GRADATION",
    "AMOUNT_WORKERS_SPARK_GRADATION",
    "CL_TYPE",
    "CITY",
    "REGION",
    "CL_PRODUCT_RKO_MARK",
    "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK",
    "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK",
    "FTS_FLAG",
    "FTS_COUNTRY_NAME",
    "FACTORING_MARK",
}
PAYMENT_COLUMNS = {"PAYER", "BENEFICIARY", "PAY_AMT", "PAY_CNT"}
PERSON_COLUMNS = {"SYNTHETIC_F", "SYNTHETIC_U"}
CASE_COMPANIES = "df_Uid_data_hack.csv"
CASE_PAYMENTS = "df_Uid_payment_hack.csv"
CASE_PERSONS = "df_Fid_Uid_link_hack.csv"


class SourceError(Exception):
    """missing — нет файла, empty — пустой, schema — нет столбцов, not_found — нет строк."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


def profile_source() -> str:
    value = os.environ.get("PROFILE_SOURCE", "auto").strip().lower() or "auto"
    if value not in {"auto", "demo", "case"}:
        raise SourceError("schema", f"Неизвестный PROFILE_SOURCE: {value}")
    return value


def using_case_extract() -> bool:
    if profile_source() == "demo":
        return False
    if profile_source() == "case" or os.environ.get("CASE_DATA_DIR"):
        return True
    return _discovered_case_dir() is not None


def read_csv(path: Path, required: set[str] | None = None) -> list[dict[str, str]]:
    layout = describe_csv(path, required or set())
    if layout is None:
        raise SourceError("not_found", f"В {_label(path)} нет строк данных")
    encoding, delimiter, columns = layout
    with path.open("r", encoding="utf-8-sig" if encoding == "utf-8" else encoding, newline="") as handle:
        rows = [
            {column: "" if raw.get(column) is None else str(raw[column]).strip() for column in columns}
            for raw in csv.DictReader(handle, delimiter=delimiter)
            if raw is not None
        ]
    if not rows:
        raise SourceError("not_found", f"В {_label(path)} нет строк данных")
    return rows


def describe_csv(path: Path, required: set[str]) -> tuple[str, str, list[str]] | None:
    """Проверяет файл и пишет в журнал имя, кодировку и столбцы. Строки данных не пишет."""
    if not path.is_file():
        raise SourceError("missing", f"Файл не найден: {_label(path)}")
    if path.stat().st_size == 0:
        raise SourceError("empty", f"Файл пустой: {_label(path)}")
    encoding = _encoding(path)
    with path.open("r", encoding=encoding, newline="") as handle:
        header = handle.readline()
        second = handle.readline()
    if not header.strip():
        raise SourceError("empty", f"Файл пустой: {_label(path)}")
    columns = [cell for cell in _header_cells(header) if cell]
    missing = sorted(required - set(columns))
    if missing:
        raise SourceError("schema", f"В {_label(path)} нет столбцов: {', '.join(missing)}")
    delimiter = ";" if header.count(";") > header.count(",") else ","
    logger.info(
        "csv file=%s encoding=%s delim=%s columns=%s",
        path.name,
        encoding,
        delimiter,
        ",".join(columns),
    )
    if not second.strip():
        return None
    return encoding, delimiter, columns


def companies_csv() -> Path:
    if not using_case_extract():
        return _ready(ROOT / "data" / "demo" / "companies.csv", COMPANY_COLUMNS)
    return _ready(_case_file(CASE_COMPANIES), COMPANY_COLUMNS)


def payments_csv() -> Path | None:
    if not using_case_extract():
        path = ROOT / "data" / "demo" / "payments.csv"
        if not path.is_file():
            return None
        return _ready(path, PAYMENT_COLUMNS)
    return _ready(_case_file(CASE_PAYMENTS), PAYMENT_COLUMNS)


def persons_csv() -> Path | None:
    if not using_case_extract():
        return None
    return _ready(_case_file(CASE_PERSONS), PERSON_COLUMNS)


def case_dir() -> Path | None:
    """Папка с CSV выгрузки. Сначала CASE_DATA_DIR, затем ../DATA, затем прежний путь."""
    raw = os.environ.get("CASE_DATA_DIR")
    if raw:
        path = Path(raw)
        if not path.is_dir():
            raise SourceError("missing", f"Папка выгрузки не найдена: {path.name or raw}")
        return path
    return _discovered_case_dir()


def bank_pdf_dir() -> Path | None:
    """PDF аналитики банка. Сейчас они остаются в Doc/Данные данные."""
    raw = os.environ.get("BANK_PDF_DIR")
    if raw:
        path = Path(raw)
        return path if path.is_dir() else None
    legacy = ROOT.parent / "Doc" / "Данные данные"
    if legacy.is_dir():
        return legacy
    return _discovered_case_dir()


def _ready(path: Path, required: set[str]) -> Path:
    if describe_csv(path, required) is None:
        raise SourceError("not_found", f"В {_label(path)} нет строк данных")
    return path


def _case_file(name: str) -> Path:
    folder = case_dir()
    if folder is None:
        raise SourceError("missing", "Папка выгрузки не найдена: DATA")
    return folder / name


def _discovered_case_dir() -> Path | None:
    data = ROOT.parent / "DATA"
    if (data / CASE_COMPANIES).is_file():
        return data
    legacy = ROOT.parent / "Doc" / "Данные данные"
    if (legacy / CASE_COMPANIES).is_file():
        return legacy
    return None


def _header_cells(header: str) -> list[str]:
    line = header.splitlines()[0] if header else ""
    separator = ";" if line.count(";") > line.count(",") else ","
    return [part.strip().lstrip("\ufeff") for part in line.split(separator)]


def _encoding(path: Path) -> str:
    raw = path.read_bytes()[:4096]
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8"
    cut = len(raw)
    while cut > 0 and (raw[cut - 1] & 0xC0) == 0x80:
        cut -= 1
    if cut > 0 and raw[cut - 1] >= 0xC0:
        cut -= 1
    try:
        raw[:cut or None].decode("utf-8")
    except UnicodeDecodeError:
        return "cp1251"
    return "utf-8"


def _label(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"
