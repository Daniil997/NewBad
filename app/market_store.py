"""SQLite persistence and retention for MOEX ISS observations."""

import json
import secrets
from datetime import datetime, timedelta, timezone

from app.user_store import app_db_path, connect_app, init_app_db, now_utc

PROFILE_MARKET_RULE_VERSION = "profile-market-v1"


def persist_profile_market_links(profile_id: str, profile_version: int, profile: dict, catalog: dict) -> list[dict]:
    """Persist transparent profile-to-instrument links; these are relevance hints, not impact forecasts."""
    focus = set(profile.get("focus_topics", []))
    tags = set(profile.get("industry_tags", []))
    currency_reason = ""
    if profile.get("currency_interest") == "interested":
        currency_reason = "В профиле отмечен интерес к валютным операциям"
    elif profile.get("export_import_interest") == "interested" or focus.intersection({"exchange_rates", "international_trade"}):
        currency_reason = "В профиле отмечены валютные расчёты или внешняя торговля"
    metals_reason = ""
    if "commodity_prices" in focus:
        metals_reason = "В профиле выбрана тема цен на сырьё и металлы"
    elif tags.intersection({"energy_raw_materials", "manufacturing", "construction", "agriculture"}):
        metals_reason = "Отраслевая связь: профиль указывает на сырьё или производство"
    reasons = {"currencies": currency_reason, "metals": metals_reason}
    rows = [
        (profile_id, profile_version, category, instrument, reason, PROFILE_MARKET_RULE_VERSION, now_utc())
        for category, reason in reasons.items() if reason
        for instrument in catalog.get(category, {}).get("instruments", [])
    ]
    init_app_db()
    with connect_app() as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO profile_market_links "
            "(profile_id, profile_version, category, instrument, reason, rule_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows,
        )
        links = connection.execute(
            "SELECT category, instrument, reason, created_at FROM profile_market_links "
            "WHERE profile_id = ? AND profile_version = ? AND rule_version = ? ORDER BY category, instrument",
            (profile_id, profile_version, PROFILE_MARKET_RULE_VERSION),
        ).fetchall()
    return [dict(row) for row in links]


def store_observations(items: list[dict]) -> int:
    if not items:
        return 0
    init_app_db()
    added = 0
    with connect_app() as connection:
        for item in items:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO market_observations
                (category, instrument, name, price, change_pct, currency, unit, source_url, source_timestamp, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (item["category"], item["instrument"], item["name"], item.get("price"),
                 item.get("change_pct"), item.get("currency", "RUB"), item.get("unit", ""),
                 item["source_url"], item["source_timestamp"], item["observed_at"]),
            )
            added += cursor.rowcount
    return added


def prune_market_data(*, retention_days: int, max_items_per_instrument: int) -> dict[str, int]:
    if not app_db_path().is_file():
        return {"removed_by_age": 0, "removed_by_count": 0, "remaining": 0}
    init_app_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    with connect_app() as connection:
        age_cursor = connection.execute("DELETE FROM market_observations WHERE observed_at < ?", (cutoff,))
        groups = connection.execute("SELECT DISTINCT category, instrument FROM market_observations").fetchall()
        removed_count = 0
        for row in groups:
            excess = connection.execute(
                "SELECT id FROM market_observations WHERE category = ? AND instrument = ? ORDER BY observed_at DESC, id DESC LIMIT -1 OFFSET ?",
                (row["category"], row["instrument"], max_items_per_instrument),
            ).fetchall()
            if excess:
                connection.executemany("DELETE FROM market_observations WHERE id = ?", [(item["id"],) for item in excess])
                removed_count += len(excess)
        remaining = int(connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0])
    return {"removed_by_age": age_cursor.rowcount, "removed_by_count": removed_count, "remaining": remaining}


def market_snapshot() -> dict:
    init_app_db()
    with connect_app() as connection:
        rows = connection.execute(
            """SELECT category, instrument, name, price, change_pct, currency, unit,
            source_url, source_timestamp, observed_at FROM (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY category, instrument ORDER BY observed_at DESC, id DESC) AS rn
              FROM market_observations
            ) WHERE rn = 1 ORDER BY category, instrument"""
        ).fetchall()
    counts: dict[str, int] = {}
    for item in rows:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return {"items": [dict(row) for row in rows], "stored_count": sum(counts.values()), "counts": counts}


def market_history(category: str, instrument: str, limit: int = 100) -> dict:
    init_app_db()
    with connect_app() as connection:
        rows = connection.execute(
            """SELECT category, instrument, name, price, change_pct, currency, unit,
            source_url, source_timestamp, observed_at FROM market_observations
            WHERE category = ? AND instrument = ? ORDER BY observed_at DESC, id DESC LIMIT ?""",
            (category, instrument, max(1, min(limit, 500))),
        ).fetchall()
    items = [dict(row) for row in reversed(rows)]
    change = None
    if len(items) >= 2 and items[0]["price"]:
        change = round((items[-1]["price"] / items[0]["price"] - 1) * 100, 4)
    return {"items": items, "change_over_period_pct": change}


def record_market_collection(run: dict) -> None:
    init_app_db()
    with connect_app() as connection:
        connection.execute(
            "INSERT INTO market_collection_runs (id, started_at, finished_at, status, details_json) VALUES (?, ?, ?, ?, ?)",
            (secrets.token_hex(16), run["started_at"], run["finished_at"], run["status"],
             json.dumps(run.get("categories", []), ensure_ascii=False, sort_keys=True)),
        )


def latest_market_collection() -> dict | None:
    if not app_db_path().is_file():
        return None
    init_app_db()
    with connect_app() as connection:
        row = connection.execute(
            "SELECT started_at, finished_at, status, details_json FROM market_collection_runs ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        categories = json.loads(row["details_json"])
    except (TypeError, json.JSONDecodeError):
        categories = []
    return {"started_at": row["started_at"], "finished_at": row["finished_at"], "status": row["status"], "categories": categories}
