"""Профиль одной компании из выгрузки кейса, без загрузки всех 85 тысяч строк в память."""

from pathlib import Path

import duckdb
import yaml

from app.db import ROOT
from app.profile.build import ClientProfile, _build_profiles, _cohort_key, _load_yaml, _money, apply_case_stats
from app.profile.sources import COMPANY_COLUMNS, companies_csv, payments_csv, persons_csv

_CACHE: dict[str, ClientProfile | None] = {}
_FINGERPRINT: tuple | None = None
_COMPANY_FIELDS = tuple(sorted(COMPANY_COLUMNS - {"SYNTHETIC_U"}))


def load_case_profiles() -> list[ClientProfile]:
    profiles = [case_profile(client_id) for client_id in _showcase()]
    return [profile for profile in profiles if profile is not None]


def case_profile(client_id: str) -> ClientProfile | None:
    client_id = client_id.strip()
    if not client_id:
        return None
    _touch_cache()
    if client_id in _CACHE:
        return _CACHE[client_id]
    row = _company_row(client_id)
    profile = None
    if row is not None:
        rules = _load_yaml(ROOT / "config" / "exposures.yaml")["exposures"]
        themes = _load_yaml(ROOT / "config" / "okved_map.yaml")["prefixes"]
        built = _build_profiles([row], _payment_rows(client_id), rules, themes)
        if built:
            share, rank, size = _aggregates(row)
            profile = _with_groups(
                apply_case_stats(
                    built[0],
                    cohort_share=share,
                    group_rank=rank,
                    group_size=size,
                    persons=_person_count(client_id),
                )
            )
    _CACHE[client_id] = profile
    return profile


def clear_case_cache() -> None:
    global _FINGERPRINT
    _CACHE.clear()
    _FINGERPRINT = None


def appendix_paths() -> list[Path]:
    from app.profile.bank_catalog import load_bank_appendix

    return [item.path for item in load_bank_appendix()]


def _showcase() -> list[str]:
    payload = yaml.safe_load((ROOT / "config" / "case_clients.yaml").read_text(encoding="utf-8"))
    return [str(item) for item in payload["clients"]]


def _company_row(client_id: str) -> dict[str, str] | None:
    rows = _records(
        companies_csv(),
        f"WITH one AS ({_companies_sql()}) SELECT * FROM one WHERE SYNTHETIC_U = ?",
        [client_id],
    )
    return rows[0] if rows else None


def _payment_rows(client_id: str) -> list[dict[str, str]]:
    """Плательщик — исходящий платёж, получатель — входящий. Суммы разных строк одной пары не складываются."""
    path = payments_csv()
    if path is None:
        return []
    return _records(
        path,
        """
        SELECT
            trim(PAYER) AS PAYER,
            trim(BENEFICIARY) AS BENEFICIARY,
            CASE
                WHEN count(DISTINCT nullif(trim(PAY_AMT), '')) <= 1 THEN max(nullif(trim(PAY_AMT), ''))
                ELSE NULL
            END AS PAY_AMT,
            CASE
                WHEN count(DISTINCT nullif(trim(PAY_CNT), '')) <= 1 THEN max(nullif(trim(PAY_CNT), ''))
                ELSE NULL
            END AS PAY_CNT
        FROM read_csv_auto(?, header=true, all_varchar=true)
        WHERE nullif(trim(PAYER), '') IS NOT NULL
          AND nullif(trim(BENEFICIARY), '') IS NOT NULL
          AND trim(PAYER) <> trim(BENEFICIARY)
          AND (trim(PAYER) = ? OR trim(BENEFICIARY) = ?)
        GROUP BY trim(PAYER), trim(BENEFICIARY)
        """,
        [client_id, client_id],
    )


def _aggregates(row: dict[str, str]) -> tuple[float | None, int | None, int | None]:
    """Доля и место берутся из REVENUE_COMPANY. Выручка группы в это поле не подставляется."""
    revenue = _money(row.get("REVENUE_COMPANY"))
    cohort = _cohort_key(row)
    share = None
    if revenue is not None and cohort:
        total = _number(
            companies_csv(),
            f"""
            WITH one AS ({_companies_sql()})
            SELECT sum(try_cast(REVENUE_COMPANY AS DOUBLE))
            FROM one
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
        f"""
        WITH one AS ({_companies_sql()})
        SELECT count(*)
        FROM one
        WHERE GROUP_ID = ?
        """,
        [group],
    )
    ahead = _number(
        companies_csv(),
        f"""
        WITH one AS ({_companies_sql()})
        SELECT count(*)
        FROM one
        WHERE GROUP_ID = ? AND try_cast(REVENUE_COMPANY AS DOUBLE) > ?
        """,
        [group, revenue],
    )
    rank = int(ahead or 0) + 1
    return share, rank, int(size or 1)


def _person_count(client_id: str) -> int:
    """Файл связей считает физлица, а не компании-контрагенты."""
    path = persons_csv()
    if path is None:
        return 0
    found = _number(
        path,
        """
        SELECT count(DISTINCT nullif(trim(SYNTHETIC_F), ''))
        FROM read_csv_auto(?, header=true, all_varchar=true)
        WHERE trim(SYNTHETIC_U) = ?
        """,
        [client_id],
    )
    return int(found or 0)


def _with_groups(profile: ClientProfile) -> ClientProfile:
    ids = [profile.client_id, *[item.client_id for item in profile.counterparties]]
    groups = _groups(ids)
    owner = groups.get(profile.client_id, "")
    links = [
        item.model_copy(update={"same_group": bool(owner and owner == groups.get(item.client_id, ""))})
        for item in profile.counterparties
    ]
    return profile.model_copy(update={"counterparties": links})


def _groups(client_ids: list[str]) -> dict[str, str]:
    if not client_ids:
        return {}
    marks = ", ".join("?" for _ in client_ids)
    rows = _records(
        companies_csv(),
        f"""
        WITH one AS ({_companies_sql()})
        SELECT SYNTHETIC_U, GROUP_ID
        FROM one
        WHERE SYNTHETIC_U IN ({marks})
        """,
        client_ids,
    )
    return {str(row["SYNTHETIC_U"]): (row.get("GROUP_ID") or "").strip() for row in rows}


def _companies_sql() -> str:
    """Один SYNTHETIC_U — одна строка. Разные значения поля гасят его, а не суммируются."""
    agreed = ", ".join(_agreed(name) for name in _COMPANY_FIELDS)
    return f"""
        SELECT trim(SYNTHETIC_U) AS SYNTHETIC_U, {agreed}
        FROM read_csv_auto(?, header=true, all_varchar=true)
        WHERE nullif(trim(SYNTHETIC_U), '') IS NOT NULL
        GROUP BY trim(SYNTHETIC_U)
    """


def _agreed(column: str) -> str:
    return (
        f"CASE WHEN count(DISTINCT nullif(trim({column}), '')) <= 1 "
        f"THEN max(nullif(trim({column}), '')) ELSE NULL END AS {column}"
    )


def _touch_cache() -> None:
    global _FINGERPRINT
    current = _fingerprint()
    if current != _FINGERPRINT:
        _CACHE.clear()
        _FINGERPRINT = current


def _fingerprint() -> tuple:
    stamps = []
    for path in (companies_csv(), payments_csv(), persons_csv()):
        if path is None or not path.is_file():
            stamps.append(None)
            continue
        stat = path.stat()
        stamps.append((str(path.resolve()), stat.st_mtime_ns, stat.st_size))
    return tuple(stamps)


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
