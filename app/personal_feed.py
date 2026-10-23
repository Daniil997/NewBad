"""Persistent, rule-based personalization for authenticated real-mode profiles."""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from app.user_store import connect_app, init_app_db, now_utc

SCORING_VERSION = "real-rules-v1"
TOPIC_EVENT_MAP = {
    "rates_inflation": {"monetary_policy", "inflation_macro", "macro", "bonds"},
    "exchange_rates": {"fx", "sanctions_trade"},
    "commodity_prices": {"commodities_energy", "metals", "precious_metals"},
    "tax_regulation": {"regulation_tax"},
    "logistics": {"sanctions_trade", "commodities_energy"},
    "international_trade": {"sanctions_trade", "fx"},
    "labor_market": {"consumption_labor"},
    "business_support": {"regulation_tax"},
    "consumer_demand": {"consumption_labor", "inflation_macro", "macro"},
    "technology_security": {"regulation_tax"},
}
INTEREST_EVENT_MAP = {
    "credit_interest": {"monetary_policy", "inflation_macro", "bonds"},
    "deposit_interest": {"monetary_policy", "inflation_macro", "bonds"},
    "currency_interest": {"fx", "sanctions_trade"},
    "export_import_interest": {"sanctions_trade", "fx", "commodities_energy"},
}
INDUSTRY_EVENT_MAP = {
    "energy_raw_materials": {"commodities_energy", "metals", "precious_metals"},
    "trade": {"fx", "sanctions_trade", "regulation_tax"},
    "transport_logistics": {"sanctions_trade", "commodities_energy"},
    "manufacturing": {"commodities_energy", "metals", "regulation_tax"},
    "agriculture": {"commodities_energy", "sanctions_trade", "regulation_tax"},
    "construction": {"metals", "commodities_energy", "monetary_policy"},
    "it_communications": {"regulation_tax", "technology_security"},
    "real_estate": {"monetary_policy", "regulation_tax"},
}
GENERIC_PROFILE_TERMS = {
    "компания", "компании", "компанию", "компаниям", "товары", "услуги", "бизнес",
    "производство", "продажа", "клиенты", "клиентами", "работаем", "основной", "основная",
    "оптовая", "розничная", "деятельность", "модель", "рынок", "регион", "россия",
}


def article_hash(item) -> str:
    content = "\n".join((item.title, item.url, item.published_at, item.excerpt, item.source or ""))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def evaluate(profile: dict, item) -> dict:
    event = item.event_type or "unknown"
    signals: list[tuple[float, str]] = []
    for topic in profile.get("focus_topics", []):
        if event in TOPIC_EVENT_MAP.get(topic, set()):
            signals.append((0.85, f"Тема профиля: {topic}"))
    for field, value in INTEREST_EVENT_MAP.items():
        if profile.get(field) == "interested" and event in value:
            label = {"credit_interest": "интерес к кредитованию и ставкам", "deposit_interest": "интерес к депозитам и ликвидности", "currency_interest": "интерес к валютным операциям", "export_import_interest": "интерес к экспорту и импорту"}[field]
            signals.append((0.75, f"Профиль отмечает {label}"))
    for tag in profile.get("industry_tags", []):
        if event in INDUSTRY_EVENT_MAP.get(tag, set()):
            signals.append((0.62, f"Отраслевая связь: {tag}"))
    if event not in {"", "unknown", None}:
        signals.append((0.38, f"Категория новости: {event}"))
    haystack = f"{item.title} {item.excerpt}".casefold()
    direct_name = str(profile.get("company_name") or "").strip()
    if len(direct_name) >= 4 and direct_name.casefold() in haystack:
        signals.append((1.0, "В новости прямо упоминается компания"))
    geography = [str(value).strip() for value in profile.get("market_geographies", [])]
    matched_geography = [value for value in geography if len(value) >= 4 and value.casefold() in haystack]
    if matched_geography:
        signals.append((0.84, f"Упомянута география бизнеса: {', '.join(matched_geography[:3])}"))
    profile_text = [profile.get(key, "") for key in ("industry", "products_services", "dependencies", "external_factors", "context_note")]
    matched_terms = sorted({term.strip(" .,;:()") for part in profile_text for term in str(part).split() if len(term.strip(" .,;:()")) >= 5 and term.strip(" .,;:()").casefold() not in GENERIC_PROFILE_TERMS and term.strip(" .,;:()").casefold() in haystack})
    if matched_terms:
        signals.append((0.9, f"Совпадение с описанием компании: {', '.join(matched_terms[:4])}"))
    signals.sort(key=lambda pair: pair[0], reverse=True)
    score = round(min(1.0, signals[0][0] if signals else 0.0), 3)
    # Any signal is explainable and relevant; unknown event categories need a stronger textual/profile match.
    relevant = any(weight >= 0.62 for weight, _ in signals)
    reasons = [reason for _, reason in signals[:3]]
    if not reasons:
        reasons = ["Не найдено подтверждённой связи с заполненными полями профиля"]
    return {"relevant": relevant, "score": score, "reasons": reasons, "method": "Правила (real-rules-v1)"}


def enqueue_job(profile_id: str, profile_version: int, candidates: list[str]) -> str | None:
    if not candidates:
        return None
    init_app_db()
    now = now_utc()
    with connect_app() as connection:
        connection.execute(
            """INSERT OR IGNORE INTO profile_analysis_jobs
            (id, profile_id, profile_version, candidate_ids_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
            (secrets.token_hex(16), profile_id, profile_version, json.dumps(candidates), now, now),
        )
        row = connection.execute(
            "SELECT id FROM profile_analysis_jobs WHERE profile_id = ? AND profile_version = ? AND status IN ('queued', 'running')",
            (profile_id, profile_version),
        ).fetchone()
    return row["id"] if row else None


def missing_candidates(profile_id: str, profile_version: int, items: list) -> list[str]:
    init_app_db()
    hashes = {item.id: article_hash(item) for item in items}
    with connect_app() as connection:
        rows = connection.execute(
            "SELECT news_id, news_hash FROM profile_news_analysis WHERE profile_id = ? AND profile_version = ? AND scoring_version = ?",
            (profile_id, profile_version, SCORING_VERSION),
        ).fetchall()
        existing = {(row["news_id"], row["news_hash"]) for row in rows}
        active = connection.execute(
            "SELECT candidate_ids_json FROM profile_analysis_jobs WHERE profile_id = ? AND profile_version = ? AND status IN ('queued', 'running')",
            (profile_id, profile_version),
        ).fetchone()
    active_ids = set(json.loads(active["candidate_ids_json"])) if active else set()
    return [item.id for item in items if (item.id, hashes[item.id]) not in existing and item.id not in active_ids]


def claim_job(lease_seconds: int = 300) -> dict | None:
    init_app_db()
    now = datetime.now(timezone.utc)
    lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
    with connect_app() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE profile_analysis_jobs SET status = 'failed', error = 'worker_lease_expired', updated_at = ?, lease_until = NULL WHERE status = 'running' AND attempts >= 3 AND lease_until < ?",
            (now.isoformat(), now.isoformat()),
        )
        candidates = connection.execute(
            """SELECT id, profile_id, profile_version, candidate_ids_json, attempts, status, updated_at, lease_until
            FROM profile_analysis_jobs WHERE attempts < 3 AND status IN ('queued', 'running')
            ORDER BY created_at"""
        ).fetchall()
        row = None
        for candidate in candidates:
            if candidate["status"] == "running":
                if candidate["lease_until"] and candidate["lease_until"] < now.isoformat():
                    row = candidate
                    break
                continue
            backoff = min(60, 5 * (2 ** max(int(candidate["attempts"]) - 1, 0))) if candidate["attempts"] else 0
            try:
                updated = datetime.fromisoformat(candidate["updated_at"])
            except (TypeError, ValueError):
                updated = now
            if now - updated >= timedelta(seconds=backoff):
                row = candidate
                break
        if not row:
            connection.commit()
            return None
        connection.execute(
            "UPDATE profile_analysis_jobs SET status = 'running', attempts = attempts + 1, updated_at = ?, lease_until = ?, error = '' WHERE id = ?",
            (now.isoformat(), lease_until, row["id"]),
        )
        connection.commit()
    return {**dict(row), "candidate_ids": json.loads(row["candidate_ids_json"])}


def save_job_results(job: dict, profile: dict, items_by_id: dict) -> None:
    init_app_db()
    created = now_utc()
    with connect_app() as connection:
        for news_id in job["candidate_ids"]:
            item = items_by_id.get(news_id)
            if item is None:
                continue
            result = evaluate(profile, item)
            connection.execute(
                """INSERT OR IGNORE INTO profile_news_analysis
                (profile_id, profile_version, news_id, news_hash, scoring_version, relevant, score, reasons_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job["profile_id"], job["profile_version"], item.id, article_hash(item), SCORING_VERSION,
                 int(result["relevant"]), result["score"], json.dumps(result, ensure_ascii=False), created),
            )
        connection.execute(
            "UPDATE profile_analysis_jobs SET status = 'succeeded', updated_at = ?, lease_until = NULL WHERE id = ?",
            (created, job["id"]),
        )


def fail_job(job_id: str, error: str) -> None:
    with connect_app() as connection:
        row = connection.execute("SELECT attempts FROM profile_analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return
        next_status = "queued" if int(row["attempts"]) < 3 else "failed"
        connection.execute(
            "UPDATE profile_analysis_jobs SET status = ?, error = ?, updated_at = ?, lease_until = NULL WHERE id = ?",
            (next_status, error[:300], now_utc(), job_id),
        )


def job_profile(job: dict) -> dict | None:
    init_app_db()
    with connect_app() as connection:
        row = connection.execute(
            "SELECT profile_json, profile_version FROM business_profiles WHERE id = ?",
            (job["profile_id"],),
        ).fetchone()
    if not row or int(row["profile_version"]) != int(job["profile_version"]):
        return None
    try:
        return json.loads(row["profile_json"])
    except (TypeError, json.JSONDecodeError):
        return {}


def analysis_results(profile_id: str, profile_version: int) -> dict[str, dict]:
    init_app_db()
    with connect_app() as connection:
        rows = connection.execute(
            """SELECT news_id, news_hash, relevant, score, reasons_json, created_at FROM profile_news_analysis
            WHERE profile_id = ? AND profile_version = ? AND scoring_version = ? ORDER BY created_at DESC""",
            (profile_id, profile_version, SCORING_VERSION),
        ).fetchall()
        job = connection.execute(
            "SELECT status, created_at, updated_at, error, candidate_ids_json FROM profile_analysis_jobs WHERE profile_id = ? AND profile_version = ? ORDER BY created_at DESC LIMIT 1",
            (profile_id, profile_version),
        ).fetchone()
    results = {}
    for row in rows:
        if row["news_id"] in results:
            continue
        try:
            payload = json.loads(row["reasons_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        results[row["news_id"]] = {
            "relevant": bool(row["relevant"]), "score": row["score"],
            "reasons": payload.get("reasons", []), "method": payload.get("method", "Правила"),
            "analyzed_at": row["created_at"], "news_hash": row["news_hash"],
        }
    job_info = dict(job) if job else None
    if job_info:
        job_info["candidate_ids"] = json.loads(job_info.pop("candidate_ids_json"))
    return {"items": results, "job": job_info}
