"""Профиль одной компании из выгрузки кейса, без загрузки всех 85 тысяч строк в память."""

from pathlib import Path

import duckdb
import yaml

from app.db import ROOT
from app.profile.build import ClientProfile, _build_profiles, _cohort_key, _load_yaml, _money, apply_case_stats
from app.profile.sources import companies_csv, payments_csv, persons_csv

_CACHE: dict[str, ClientProfile] = {}


def load_case_profiles() -> list[ClientProfile]:
    profiles = [case_profile(client_id) for client_id in _showcase()]
    return [profile for profile in profiles if profile is not None]


def case_profile(client_id: str) -> ClientProfile | None:
    cached = _CACHE.get(client_id)
    if cached is not None:
        return cached
    row = _company_row(client_id)
    if row is None:
        return None
    rules = _load_yaml(ROOT / "config" / "exposures.yaml")["exposures"]
    themes = _load_yaml(ROOT / "config" / "okved_map.yaml")["prefixes"]
    built = _build_profiles([row], _payment_rows(client_id), rules, themes)
    if not built:
        return None
    share, rank, size = _aggregates(row)
    profile = apply_case_stats(
        built[0],
        cohort_share=share,
        group_rank=rank,
        group_size=size,
        persons=_person_count(client_id),
    )
    _CACHE[client_id] = profile
    return profile


def appendix_paths() -> list[Path]:
    from app.profile.sources import bank_pdf_dir

    folder = bank_pdf_dir()
    if folder is None:
        return []
    payload = yaml.safe_load((ROOT / "config" / "bank_appendix.yaml").read_text(encoding="utf-8"))
    return [folder / str(item["file"]) for item in payload["documents"]]


def _showcase() -> list[str]:
    payload = yaml.safe_load((ROOT / "config" / "case_clients.yaml").read_text(encoding="utf-8"))
    return [str(item) for item in payload["clients"]]


def _company_row(client_id: str) -> dict[str, str] | None:
    rows = _records(
        companies_csv(),
        "SELECT * FROM read_csv_auto(?, header=true, all_varchar=true) WHERE SYNTHETIC_U = ?",
        [client_id],
    )
    return rows[0] if rows else None


def _payment_rows(client_id: str) -> list[dict[str, str]]:
    path = payments_csv()
    if path is None:
        return []
    return _records(
        path,
        """
        SELECT * FROM read_csv_auto(?, header=true, all_varchar=true)
        WHERE PAYER = ? OR BENEFICIARY = ?
        """,
        [client_id, client_id],
    )


def _aggregates(row: dict[str, str]) -> tuple[float | None, int | None, int | None]:
    revenue = _money(row.get("REVENUE_COMPANY"))
    cohort = _cohort_key(row)
    share = None
    if revenue is not None and cohort:
        total = _number(
            companies_csv(),
            """
            SELECT sum(try_cast(REVENUE_COMPANY AS DOUBLE))
            FROM read_csv_auto(?, header=true, all_varchar=true)
            WHERE split_part(MAINOKVED_CODE, '.', 1) = ?
            """,
            [cohort],
        )
        if total:
            share = round(revenue / total, 4)
    group = (row.get("GROUP_ID") or "").strip()
    if not group or revenue is None:
        return share, None, None
    size = _number(
        companies_csv(),
        """
        SELECT count(*)
        FROM read_csv_auto(?, header=true, all_varchar=true)
        WHERE GROUP_ID = ?
        """,
        [group],
    )
    ahead = _number(
        companies_csv(),
        """
        SELECT count(*)
        FROM read_csv_auto(?, header=true, all_varchar=true)
        WHERE GROUP_ID = ? AND try_cast(REVENUE_COMPANY AS DOUBLE) > ?
        """,
        [group, revenue],
    )
    rank = int(ahead or 0) + 1
    return share, rank, int(size or 1)


def _person_count(client_id: str) -> int:
    path = persons_csv()
    if path is None:
        return 0
    found = _number(
        path,
        """
        SELECT count(*)
        FROM read_csv_auto(?, header=true, all_varchar=true)
        WHERE SYNTHETIC_U = ?
        """,
        [client_id],
    )
    return int(found or 0)


def _number(path, sql: str, params: list) -> float | None:
    connection = duckdb.connect()
    try:
        row = connection.execute(sql, [str(path), *params]).fetchone()
    finally:
        connection.close()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def _records(path, sql: str, params: list) -> list[dict[str, str]]:
    connection = duckdb.connect()
    try:
        cursor = connection.execute(sql, [str(path), *params])
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()
