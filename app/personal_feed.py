"""Persistent, model-assisted personalization for authenticated real-mode profiles."""

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from app.profile.real_taxonomy import REAL_PROFILE_TAXONOMY
from app.user_store import connect_app, init_app_db, now_utc

BASE_SCORING_VERSION = "real-llm-v3"


def scoring_version() -> str:
    from app.user_store import developer_settings

    selection = developer_settings().get("task_routes", {}).get("real", {}).get("news_selection", "mock")
    return f"{BASE_SCORING_VERSION}:{selection}"
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
    "leasing_interest": {"monetary_policy", "bonds", "regulation_tax"},
    "guarantees_interest": {"sanctions_trade", "regulation_tax"},
    "factoring_interest": {"monetary_policy", "consumption_labor", "regulation_tax"},
    "settlements_interest": {"fx", "sanctions_trade", "regulation_tax"},
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


# Single relevance scale shared by the rules, model arbitration and the frontend:
# 0–0.29: no substantiated link; 0.30–0.54: weak; 0.55–0.79: link; 0.80–1: strong.
WEAK_LINK = 0.30
CLEAR_LINK = 0.55
STRONG_LINK = 0.80

INTEREST_LABELS = {
    "credit_interest": "интерес к кредитам и стоимости финансирования",
    "deposit_interest": "интерес к депозитам и ликвидности",
    "currency_interest": "интерес к валютным операциям",
    "export_import_interest": "интерес к внешней торговле",
    "leasing_interest": "интерес к лизингу",
    "guarantees_interest": "интерес к банковским гарантиям",
    "factoring_interest": "интерес к факторингу",
    "settlements_interest": "интерес к расчётам и платежам",
}

TOPIC_LABELS = {
    entry["id"]: entry["label"] for entry in REAL_PROFILE_TAXONOMY["focus_topics"]
}
INDUSTRY_LABELS = {
    entry["id"]: entry["label"] for entry in REAL_PROFILE_TAXONOMY["industry_tags"]
}


def _has_phrase(haystack: str, phrase: str) -> bool:
    words = re.findall(r"[a-zа-яё0-9]+", phrase.casefold())
    if not words:
        return False
    pattern = r"(?<!\w)" + r"\W+".join(re.escape(word) for word in words) + r"(?!\w)"
    return bool(re.search(pattern, haystack))


def _specific_terms(value: str) -> list[str]:
    return [
        word for word in re.findall(r"[a-zа-яё0-9-]+", value.casefold())
        if len(word) >= 6 and word not in GENERIC_PROFILE_TERMS
        and word not in {"россии", "новости", "компаний", "продукции", "предприятия", "экономика"}
    ]


def evaluate(profile: dict, item) -> dict:
    """Deterministic, evidence-led relevance. One incidental word cannot yield a strong link."""
    event = item.event_type or "unknown"
    text = f"{item.title} {item.excerpt}".casefold().replace("ё", "е")
    signals: list[tuple[str, float, str]] = []

    def add(key: str, weight: float, reason: str) -> None:
        if not any(signal[0] == key for signal in signals):
            signals.append((key, weight, reason))

    company = str(profile.get("company_name") or "").strip()
    if len(company) >= 7 and _has_phrase(text, company.replace("ё", "е")):
        add("company", 0.95, "Компания прямо названа в публикации")

    for topic in profile.get("focus_topics", []):
        if event in TOPIC_EVENT_MAP.get(topic, set()):
            # Broad regulatory stories are not automatically about IT or support.
            if topic == "technology_security" and not re.search(r"цифров|кибер|информацион|программ|технолог", text):
                continue
            if topic == "business_support" and not re.search(r"поддержк|субсиди|льгот|грант|госзакуп", text):
                continue
            add("focus", 0.68, f"Отслеживаемая тема профиля: {TOPIC_LABELS.get(topic, topic)}")
            break
    for field, topics in INTEREST_EVENT_MAP.items():
        if profile.get(field) == "interested" and event in topics:
            add("bank_need", 0.64, f"Указан {INTEREST_LABELS[field]}")
            break
    for tag in profile.get("industry_tags", []):
        if event in INDUSTRY_EVENT_MAP.get(tag, set()):
            if tag == "it_communications" and event == "regulation_tax" and not re.search(r"цифров|связ|информацион|кибер|программ", text):
                continue
            add("industry", 0.52, f"Возможна отраслевая связь: {INDUSTRY_LABELS.get(tag, tag)}")
            break

    regions = [
        str(value).strip() for value in [
            profile.get("country"), profile.get("region"), profile.get("city"),
            *(profile.get("market_geographies") or []),
        ]
        if value and len(str(value).strip()) >= 5
    ]
    matches = [value for value in regions if _has_phrase(text, value.replace("ё", "е"))]
    if matches:
        add("geography", 0.38, f"В публикации упомянут рынок или регион компании: {', '.join(matches[:2])}")

    # Require two different specific words, not one incidental substring.
    profile_terms = {
        word for field in ("industry", "products_services", "dependencies", "external_factors", "context_note")
        for word in _specific_terms(str(profile.get(field) or "").replace("ё", "е"))
    }
    matches = sorted(term for term in profile_terms if _has_phrase(text, term))
    if len(matches) >= 2:
        add("business", 0.72, f"Совпадают признаки деятельности: {', '.join(matches[:3])}")
    if event not in ("", "unknown"):
        add("category", 0.12, "Определена категория публикации; сама по себе она не доказывает связь")

    signals.sort(key=lambda signal: signal[1], reverse=True)
    substantial = [signal for signal in signals if signal[0] != "category"]
    if substantial:
        score = substantial[0][1]
        # Independent signals reinforce a link; repeated tags in one category do not.
        score += 0.13 if len(substantial) > 1 else 0
        score += 0.07 if len(substantial) > 2 else 0
        score = round(min(0.98, score), 3)
    else:
        score = 0.12 if signals else 0.0
    reasons = [signal[2] for signal in substantial[:3]]
    if not reasons:
        reasons = ["Связь с заполненными признаками профиля пока не подтверждена"]
    return {
        "relevant": score >= WEAK_LINK,
        "score": score,
        "reasons": reasons,
        "method": "Проверяемые признаки профиля (real-rules-v3)",
    }


def calibrate_model_result(profile: dict, item, model: dict | None) -> dict:
    """Use LLM only as a bounded supporting judgement, not as unverified certainty."""
    baseline = evaluate(profile, item)
    if model is None:
        return baseline
    try:
        score = float(model["score"])
    except (KeyError, ValueError, TypeError):
        return baseline
    if not 0 <= score <= 1:
        return baseline
    if baseline["score"] < WEAK_LINK:
        # A model cannot promote an unsupported category match to a factual link.
        return baseline
    final_score = round(0.75 * baseline["score"] + 0.25 * score, 3)
    explanation = str((model.get("reasons") or [""])[0]).strip()
    reasons = list(baseline["reasons"])
    if explanation and explanation not in reasons:
        reasons.append(f"Оценка модели: {explanation[:350]}")
    return {
        "relevant": final_score >= WEAK_LINK,
        "score": final_score,
        "reasons": reasons[:4],
        "method": "Правила + оценка модели",
    }


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
            (profile_id, profile_version, scoring_version()),
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


def save_job_results(job: dict, profile: dict, items_by_id: dict, model_results: dict[str, dict] | None = None) -> None:
    init_app_db()
    created = now_utc()
    with connect_app() as connection:
        for news_id in job["candidate_ids"]:
            item = items_by_id.get(news_id)
            if item is None:
                continue
            result = calibrate_model_result(profile, item, (model_results or {}).get(news_id))
            connection.execute(
                """INSERT OR IGNORE INTO profile_news_analysis
                (profile_id, profile_version, news_id, news_hash, scoring_version, relevant, score, reasons_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job["profile_id"], job["profile_version"], item.id, article_hash(item), scoring_version(),
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
            (profile_id, profile_version, scoring_version()),
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
