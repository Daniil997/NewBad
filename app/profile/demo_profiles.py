"""Editable demo profiles, isolated from accounts and source CSV fixtures."""
import json
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

from app.profile.details import BANK_FIELDS, PRESENCE_FIELDS, extended_seed
from app.user_store import app_db_path, business_profile_for_owner, now_utc, profile_database

if TYPE_CHECKING:
    from app.profile.build import ClientProfile


def demo_database_path():
    original = app_db_path()
    return original.with_name(original.stem + ".demo-profiles.sqlite3")


def seed_profile(original: "ClientProfile") -> dict:
    values = {item.id: item.value for item in original.features}
    def interest(key: str) -> str:
        return "interested" if values.get(key) == "есть" else "unknown"
    return {
        **extended_seed(original),
        "company_name": original.title, "city": values.get("CITY", ""), "region": values.get("REGION", ""),
        "industry": values.get("MAINOKVED_NAME", ""), "okved": values.get("MAINOKVED_CODE", ""),
        "size_band": values.get("AMOUNT_WORKERS_FNS_GRADATION", values.get("AMOUNT_WORKERS_SPARK_GRADATION", "")),
        "business_stage": values.get("ECO_MARK_LABEL", ""),
        "credit_interest": interest("CL_PRODUCT_LOA_MARK"), "deposit_interest": interest("CL_PRODUCT_DEP_MARK"),
        "leasing_interest": interest("CL_PRODUCT_LEA_MARK"), "guarantees_interest": interest("CL_PRODUCT_GAR_MARK"),
        "factoring_interest": interest("FACTORING_MARK"), "settlements_interest": interest("CL_PRODUCT_RKO_MARK"),
        "export_import_interest": interest("FTS_FLAG"),
        "market_geographies": [values["FTS_COUNTRY_NAME"]] if values.get("FTS_COUNTRY_NAME") else [],
    }


@contextmanager
def demo_profile_context(original: "ClientProfile"):
    path = demo_database_path()
    with sqlite3.connect(path, timeout=5) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS business_profiles (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL UNIQUE,
                profile_json TEXT NOT NULL DEFAULT '{}', profile_version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'incomplete', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profile_field_sources (
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                field_name TEXT NOT NULL, value_json TEXT, source_url TEXT, source_title TEXT,
                extracted_at TEXT, confidence REAL, review_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(profile_id, field_name, source_url)
            );
            CREATE TABLE IF NOT EXISTS profile_site_analysis_runs (
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
                pages_scanned INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT ''
            );
        """)
        now = now_utc()
        connection.execute(
            "INSERT OR IGNORE INTO business_profiles (id, owner_id, profile_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (original.client_id, original.client_id, json.dumps(seed_profile(original), ensure_ascii=False), now, now),
        )
        # Earlier versions saved only the compact editor fields. Backfill new
        # keys once, preserving explicit blanks and all existing manual edits.
        row = connection.execute("SELECT profile_json FROM business_profiles WHERE owner_id = ?", (original.client_id,)).fetchone()
        current = json.loads(row[0])
        additions = {key: value for key, value in extended_seed(original).items() if key not in current}
        if additions:
            connection.execute(
                "UPDATE business_profiles SET profile_json = ?, profile_version = profile_version + 1, updated_at = ? WHERE owner_id = ?",
                (json.dumps({**additions, **current}, ensure_ascii=False), now, original.client_id),
            )
    if additions:
        invalidate_demo_feed(original.client_id)
    with profile_database(path):
        yield original.client_id


def overlay_profile(original: "ClientProfile") -> "ClientProfile":
    """Use saved edits in demo news and cards, retaining bank facts and their provenance."""
    path = demo_database_path()
    if not path.is_file():
        return original
    with profile_database(path):
        saved = business_profile_for_owner(original.client_id)
    if not saved or saved["profile_version"] <= 1:
        return original
    from app.profile.build import (
        FIELD_LABELS, WATCHED_FIELDS, Counterparty, Exposure, Feature,
        _counts_as_missing, _exposures, _load_yaml, _money_role,
    )
    from app.db import ROOT

    profile = saved["profile"]
    mapped = {"city": "CITY", "region": "REGION", "industry": "MAINOKVED_NAME", "okved": "MAINOKVED_CODE",
              "business_stage": "ECO_MARK_LABEL"}
    mapped.update({key: feature_id for feature_id, key in BANK_FIELDS.items()})
    seeded = seed_profile(original)
    features = {feature.id: feature for feature in original.features}
    facts_changed = any(profile.get(key) != seeded.get(key) for key in mapped if key in profile)
    for key, value in profile.items():
        if key == "counterparties":
            continue
        feature_id = mapped.get(key, key)
        if key in mapped and value == seeded.get(key):
            continue
        if key in mapped:
            features.pop(feature_id, None)
        if value is not None and value != "" and value != [] and value != "unknown" and value is not False:
            rendered = {"yes": "есть", "no": "нет"}.get(value, value) if key in PRESENCE_FIELDS else value
            features[feature_id] = Feature(id=feature_id, label=FIELD_LABELS.get(feature_id, key),
                                          value=", ".join(rendered) if isinstance(rendered, list) else str(rendered),
                                          origin="client_input", source_fields=[key])
    counterparties = [Counterparty.model_validate({**row, "same_group": bool(row.get("same_group"))})
                      for row in profile.get("counterparties", [row.model_dump() for row in original.counterparties])]
    row = {key: {"есть": "1", "нет": "0"}.get(feature.value, feature.value) for key, feature in features.items()}
    role = _money_role(row) if facts_changed else original.money_role
    if facts_changed:
        features["money_role"] = Feature(id="money_role", label="Роль по стоимости денег", value=role,
                                         origin="derived", source_fields=["CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK"])
    # Rebuild links when actual facts change. Preferences remain a separate hypothesis below.
    if facts_changed or counterparties != original.counterparties:
        rules = _load_yaml(ROOT / "config" / "exposures.yaml")["exposures"]
        exposures = {exposure.id: exposure for exposure in _exposures(row, rules, row.get("okved_theme"), counterparties)}
        for exposure in exposures.values():
            if ("counterparties" in exposure.feature_refs and counterparties != original.counterparties
                    or any(features.get(ref) and features[ref].origin == "client_input" for ref in exposure.feature_refs)):
                exposure.source = "client_input"
    else:
        exposures = {exposure.id: exposure for exposure in original.exposures}
    interest_exposures = [
        (("credit_interest", "leasing_interest"), "rate_borrower", "Интерес к финансированию"),
        (("deposit_interest",), "rate_saver", "Интерес к депозитам"),
        (("currency_interest",), "fx_exposure", "Интерес к валютным операциям"),
        (("export_import_interest",), "foreign_trade", "Интерес к внешней торговле"),
        (("factoring_interest",), "liquidity_factoring", "Интерес к факторингу"),
    ]
    for keys, exposure_id, title in interest_exposures:
        interested = [key for key in keys if profile.get(key) == "interested"]
        if (not interested and any(profile.get(key) == "not_relevant" for key in keys)
                and exposure_id in exposures and exposures[exposure_id].basis == "hypothesis"
                and exposures[exposure_id].source == "client_input"):
            exposures.pop(exposure_id, None)
        elif interested and exposure_id not in exposures:
            exposures[exposure_id] = Exposure(id=exposure_id, title=title, basis="hypothesis", source="client_input",
                                             confidence="medium", strength=.6, feature_refs=interested,
                                             assumptions=["Выбран интерес к теме; наличие банковского продукта этим не подтверждается."])
    return original.model_copy(update={
        "title": profile.get("company_name") or original.title, "features": list(features.values()),
        "exposures": list(exposures.values()), "counterparties": counterparties,
        "money_role": role,
        "cohort_key": profile.get("cohort_key") or None,
        "cohort_share": profile.get("cohort_revenue_share", original.cohort_share),
        "missing_data": [key for key in WATCHED_FIELDS if key not in features and _counts_as_missing(key, row)],
    })


def invalidate_demo_feed(client_id: str) -> None:
    from app.db import connect, init_db
    init_db()
    with connect() as connection:
        connection.execute("DELETE FROM feed_cache WHERE client_id = ?", (client_id,))
