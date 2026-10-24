"""SQLite persistence for real-mode accounts. Kept separate from the demo journal."""

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSION_DAYS = 14
RATE_WINDOW = timedelta(minutes=15)
RATE_LIMIT = 10
SITE_SCAN_COOLDOWN = timedelta(minutes=5)
SITE_SCAN_STALE_AFTER = timedelta(minutes=10)
DEFAULT_DEVELOPER_SETTINGS = {
    "validate_numeric_grounding": True,
    "task_routes": {
        "real": {"profile_site": "mock", "news_report": "mock", "news_selection": "mock", "chat": "mock"},
        "demo": {"news_card": "mock", "news_card_fact": "mock", "news_card_copy": "mock", "chat": "mock"},
    },
    "news_lookback_days": 3,
    "news_retention_days": 90,
    "news_max_items": 500,
    "news_collection_interval_hours": 0,
    "market_retention_days": 90,
    "market_max_items_per_instrument": 1000,
    "market_collection_interval_hours": 0,
    "market_categories": ["currencies", "equities", "metals"],
    "personal_analysis_trigger": "first_then_manual",
    "personal_analysis_interval_hours": 24,
    "personal_analysis_max_candidates": 30,
    "insight_refresh_mode": "manual",
    "insight_refresh_interval_hours": 24,
    "insight_retention_days": 90,
}


def app_db_path() -> Path:
    configured = os.environ.get("APP_DB_PATH", "").strip()
    path = Path(configured) if configured else ROOT / "data" / "app.sqlite3"
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect_app() -> sqlite3.Connection:
    connection = sqlite3.connect(app_db_path(), timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def init_app_db() -> None:
    with connect_app() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > 10:
        raise RuntimeError(f"Unsupported app database version: {version}")
    if version == 0:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    login TEXT NOT NULL UNIQUE COLLATE BINARY,
                    password_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX sessions_user_id_idx ON sessions(user_id);
                CREATE INDEX sessions_expires_at_idx ON sessions(expires_at);
                CREATE TABLE user_settings (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    preferences_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE business_profiles (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    profile_version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'incomplete',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE profile_field_sources (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                    field_name TEXT NOT NULL,
                    value_json TEXT,
                    source_url TEXT,
                    source_title TEXT,
                    extracted_at TEXT,
                    confidence REAL,
                    review_status TEXT NOT NULL DEFAULT 'unreviewed',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(profile_id, field_name, source_url)
                );
                CREATE TABLE auth_rate_limits (
                    key_hash TEXT PRIMARY KEY,
                    window_started_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                PRAGMA user_version = 1;
                COMMIT;
                """
            )
        version = 1
    if version == 1:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE profile_site_analysis_runs (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
                    pages_scanned INTEGER NOT NULL DEFAULT 0,
                    model TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX profile_site_runs_profile_idx
                    ON profile_site_analysis_runs(profile_id, started_at DESC);
                PRAGMA user_version = 2;
                COMMIT;
                """
            )
        version = 2
    if version == 2:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE developer_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    settings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE news_collection_runs (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                PRAGMA user_version = 3;
                COMMIT;
                """
            )
        version = 3
    if version == 3:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE news_articles (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    loaded_at TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX news_articles_published_idx ON news_articles(published_at DESC, loaded_at DESC);
                PRAGMA user_version = 4;
                COMMIT;
                """
            )
        version = 4
    if version == 4:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE market_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price REAL,
                    change_pct REAL,
                    currency TEXT NOT NULL DEFAULT 'RUB',
                    unit TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL,
                    source_timestamp TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    UNIQUE(category, instrument, source_timestamp)
                );
                CREATE INDEX market_observations_category_time_idx
                    ON market_observations(category, observed_at DESC);
                CREATE TABLE market_collection_runs (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                PRAGMA user_version = 5;
                COMMIT;
                """
            )
        version = 5
    if version == 5:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE profile_news_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                    profile_version INTEGER NOT NULL,
                    news_id TEXT NOT NULL,
                    news_hash TEXT NOT NULL,
                    scoring_version TEXT NOT NULL,
                    relevant INTEGER NOT NULL CHECK (relevant IN (0, 1)),
                    score REAL NOT NULL,
                    reasons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(profile_id, profile_version, news_id, news_hash, scoring_version)
                );
                CREATE INDEX profile_news_analysis_feed_idx
                    ON profile_news_analysis(profile_id, profile_version, score DESC, created_at DESC);
                CREATE TABLE profile_analysis_jobs (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                    profile_version INTEGER NOT NULL,
                    candidate_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lease_until TEXT,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE UNIQUE INDEX one_active_profile_analysis_job
                    ON profile_analysis_jobs(profile_id, profile_version)
                    WHERE status IN ('queued', 'running');
                PRAGMA user_version = 6;
                COMMIT;
                """
            )
        version = 6
    if version == 6:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE profile_chat_sessions (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                    profile_version INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT 'Общий чат по бизнесу',
                    focused_news_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX profile_chat_sessions_owner_idx
                    ON profile_chat_sessions(owner_id, updated_at DESC);
                CREATE TABLE profile_chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES profile_chat_sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    text TEXT NOT NULL,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX profile_chat_messages_session_idx
                    ON profile_chat_messages(session_id, id);
                PRAGMA user_version = 7;
                COMMIT;
                """
            )
        version = 7
    if version == 7:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE profile_insight_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                    profile_version INTEGER NOT NULL,
                    input_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(profile_id, profile_version, input_hash)
                );
                CREATE INDEX profile_insight_latest_idx
                    ON profile_insight_snapshots(profile_id, profile_version, created_at DESC);
                PRAGMA user_version = 8;
                COMMIT;
                """
            )
        version = 8
    if version == 8:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE profile_news_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                    profile_version INTEGER NOT NULL,
                    news_id TEXT NOT NULL,
                    news_hash TEXT NOT NULL,
                    report_version TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(profile_id, profile_version, news_id, news_hash, report_version)
                );
                CREATE INDEX profile_news_reports_latest_idx
                    ON profile_news_reports(profile_id, profile_version, news_id, created_at DESC);
                PRAGMA user_version = 9;
                COMMIT;
                """
            )
        version = 9
    if version == 9:
        with connect_app() as connection:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE profile_market_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
                    profile_version INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    rule_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(profile_id, profile_version, category, instrument, rule_version)
                );
                CREATE INDEX profile_market_links_profile_idx
                    ON profile_market_links(profile_id, profile_version, category, instrument);
                PRAGMA user_version = 10;
                COMMIT;
                """
            )


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def developer_settings() -> dict:
    with connect_app() as connection:
        row = connection.execute("SELECT settings_json FROM developer_settings WHERE id = 1").fetchone()
    if not row:
        return dict(DEFAULT_DEVELOPER_SETTINGS)
    try:
        stored = json.loads(row["settings_json"])
    except (TypeError, json.JSONDecodeError):
        stored = {}
    merged = {**DEFAULT_DEVELOPER_SETTINGS, **stored}
    merged["task_routes"] = {
        space: {
            task: ("mock" if route == "inherit" else route)
            for task, route in {
                **DEFAULT_DEVELOPER_SETTINGS["task_routes"][space],
                **(stored.get("task_routes", {}).get(space) or {}),
            }.items()
        }
        for space in DEFAULT_DEVELOPER_SETTINGS["task_routes"]
    }
    return merged


def save_developer_settings(settings: dict) -> dict:
    merged = {**DEFAULT_DEVELOPER_SETTINGS, **settings}
    merged["task_routes"] = {
        space: {**DEFAULT_DEVELOPER_SETTINGS["task_routes"][space], **(settings.get("task_routes", {}).get(space) or {})}
        for space in DEFAULT_DEVELOPER_SETTINGS["task_routes"]
    }
    with connect_app() as connection:
        connection.execute(
            "INSERT INTO developer_settings (id, settings_json, updated_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET settings_json = excluded.settings_json, updated_at = excluded.updated_at",
            (json.dumps(merged, ensure_ascii=False, sort_keys=True), now_utc()),
        )
    return merged


def prune_insight_snapshots(retention_days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))).isoformat()
    with connect_app() as connection:
        cursor = connection.execute("DELETE FROM profile_insight_snapshots WHERE created_at < ?", (cutoff,))
    return cursor.rowcount


def record_news_collection(run: dict) -> None:
    with connect_app() as connection:
        connection.execute(
            "INSERT INTO news_collection_runs (id, started_at, finished_at, status, details_json) VALUES (?, ?, ?, ?, ?)",
            (
                secrets.token_hex(16), run["started_at"], run["finished_at"], run["status"],
                json.dumps(run.get("sources", []), ensure_ascii=False, sort_keys=True),
            ),
        )


def latest_news_collection() -> dict | None:
    with connect_app() as connection:
        row = connection.execute(
            "SELECT started_at, finished_at, status, details_json FROM news_collection_runs ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        sources = json.loads(row["details_json"])
    except (TypeError, json.JSONDecodeError):
        sources = []
    return {
        "started_at": row["started_at"], "finished_at": row["finished_at"],
        "status": row["status"], "sources": sources,
    }


def create_user(login: str, password_hash: str) -> dict:
    user_id = secrets.token_hex(16)
    now = now_utc()
    with connect_app() as connection:
        connection.execute(
            "INSERT INTO users (id, login, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, login, password_hash, now, now),
        )
        connection.execute(
            "INSERT INTO user_settings (user_id, updated_at) VALUES (?, ?)",
            (user_id, now),
        )
        connection.execute(
            "INSERT INTO business_profiles (id, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (secrets.token_hex(16), user_id, now, now),
        )
        return {"id": user_id, "login": login, "created_at": now}


def user_by_login(login: str) -> dict | None:
    with connect_app() as connection:
        row = connection.execute(
            "SELECT id, login, password_hash, status, created_at FROM users WHERE login = ?",
            (login,),
        ).fetchone()
    return dict(row) if row else None


def user_for_session(token: str) -> dict | None:
    now = now_utc()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect_app() as connection:
        row = connection.execute(
            """
            SELECT users.id, users.login, users.created_at, sessions.token_hash
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.revoked_at IS NULL
              AND sessions.expires_at > ? AND users.status = 'active'
            """,
            (token_hash, now),
        ).fetchone()
        if row:
            connection.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))
    return dict(row) if row else None


def create_session(user_id: str) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    created = datetime.now(timezone.utc)
    expires = created + timedelta(days=SESSION_DAYS)
    with connect_app() as connection:
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
            (token_hash, user_id, created.isoformat(), expires.isoformat(), created.isoformat()),
        )
    return token, SESSION_DAYS * 24 * 60 * 60


def revoke_session(token: str) -> None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect_app() as connection:
        connection.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ?", (now_utc(), token_hash))


def business_profile_for_owner(user_id: str) -> dict | None:
    with connect_app() as connection:
        row = connection.execute(
            "SELECT id, profile_json, profile_version, status, created_at, updated_at "
            "FROM business_profiles WHERE owner_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    try:
        profile = json.loads(row["profile_json"])
    except (TypeError, json.JSONDecodeError):
        profile = {}
    return {
        "profile_id": row["id"],
        "profile": profile,
        "profile_version": int(row["profile_version"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_business_profile(user_id: str, profile: dict, status: str) -> dict | None:
    serialized = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    now = now_utc()
    with connect_app() as connection:
        row = connection.execute(
            "SELECT profile_json, profile_version FROM business_profiles WHERE owner_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        current = row["profile_json"]
        version = int(row["profile_version"])
        if current != serialized:
            version += 1
        connection.execute(
            "UPDATE business_profiles SET profile_json = ?, profile_version = ?, status = ?, updated_at = ? "
            "WHERE owner_id = ?",
            (serialized, version, status, now, user_id),
        )
    return business_profile_for_owner(user_id)


def save_profile_suggestions(user_id: str, suggestions: list[dict]) -> list[dict]:
    now = now_utc()
    saved: list[dict] = []
    with connect_app() as connection:
        profile_row = connection.execute(
            "SELECT id, profile_json FROM business_profiles WHERE owner_id = ?",
            (user_id,),
        ).fetchone()
        if not profile_row:
            return saved
        profile_id = profile_row["id"]
        try:
            profile = json.loads(profile_row["profile_json"])
        except (TypeError, json.JSONDecodeError):
            profile = {}
        connection.execute(
            "DELETE FROM profile_field_sources WHERE profile_id = ? AND review_status = 'pending'",
            (profile_id,),
        )
        for item in suggestions:
            field_name = item["field_name"]
            current_value = profile.get(field_name)
            if current_value not in (None, "", []):
                continue
            value_json = json.dumps(
                {"value": item["value"], "evidence": item["evidence"]},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO profile_field_sources (
                    id, profile_id, field_name, value_json, source_url, source_title,
                    extracted_at, confidence, review_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(profile_id, field_name, source_url) DO UPDATE SET
                    value_json = excluded.value_json,
                    source_title = excluded.source_title,
                    extracted_at = excluded.extracted_at,
                    confidence = excluded.confidence,
                    review_status = 'pending',
                    updated_at = excluded.updated_at
                WHERE profile_field_sources.review_status = 'pending'
                """,
                (
                    secrets.token_hex(16), profile_id, field_name, value_json,
                    item["source_url"], item["source_title"], now, item["confidence"], now, now,
                ),
            )
            row = connection.execute(
                """
                SELECT field_name, value_json, source_url, source_title, extracted_at, confidence
                FROM profile_field_sources
                WHERE profile_id = ? AND field_name = ? AND source_url = ? AND review_status = 'pending'
                """,
                (profile_id, field_name, item["source_url"]),
            ).fetchone()
            if row:
                saved.append(_suggestion_dict(row))
    return saved


def pending_profile_suggestions(user_id: str) -> list[dict]:
    with connect_app() as connection:
        rows = connection.execute(
            """
            SELECT field_name, value_json, source_url, source_title, extracted_at, confidence
            FROM profile_field_sources
            WHERE profile_id = (SELECT id FROM business_profiles WHERE owner_id = ?)
              AND review_status = 'pending'
            ORDER BY field_name, source_url
            """,
            (user_id,),
        ).fetchall()
    return [_suggestion_dict(row) for row in rows]


def decide_profile_suggestion(user_id: str, field_name: str, source_url: str, *, accept: bool) -> str:
    allowed_fields = {
        "company_name", "country", "region", "city", "description", "industry",
        "industry_tags", "products_services", "customer_types", "business_model",
    }
    if field_name not in allowed_fields:
        return "not_found"
    now = now_utc()
    with connect_app() as connection:
        profile_row = connection.execute(
            "SELECT id, profile_json, profile_version FROM business_profiles WHERE owner_id = ?",
            (user_id,),
        ).fetchone()
        if not profile_row:
            return "not_found"
        source = connection.execute(
            """
            SELECT id, value_json FROM profile_field_sources
            WHERE profile_id = ? AND field_name = ? AND source_url = ? AND review_status = 'pending'
            """,
            (profile_row["id"], field_name, source_url),
        ).fetchone()
        if not source:
            return "not_found"
        if not accept:
            connection.execute(
                "UPDATE profile_field_sources SET review_status = 'rejected', updated_at = ? WHERE id = ?",
                (now, source["id"]),
            )
            return "rejected"
        try:
            profile = json.loads(profile_row["profile_json"])
            proposal = json.loads(source["value_json"])
        except (TypeError, json.JSONDecodeError):
            return "not_found"
        current_value = profile.get(field_name)
        if current_value not in (None, "", []):
            return "conflict"
        profile[field_name] = proposal.get("value")
        serialized = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        status = "complete" if profile.get("company_name") and (profile.get("description") or profile.get("description_skipped")) else "incomplete"
        connection.execute(
            "UPDATE business_profiles SET profile_json = ?, profile_version = ?, status = ?, updated_at = ? WHERE id = ?",
            (serialized, int(profile_row["profile_version"]) + 1, status, now, profile_row["id"]),
        )
        connection.execute(
            "UPDATE profile_field_sources SET review_status = 'accepted', updated_at = ? WHERE id = ?",
            (now, source["id"]),
        )
    return "accepted"


def _suggestion_dict(row: sqlite3.Row) -> dict:
    try:
        value = json.loads(row["value_json"])
    except (TypeError, json.JSONDecodeError):
        value = {}
    return {
        "field_name": row["field_name"],
        "value": value.get("value"),
        "evidence": value.get("evidence", ""),
        "source_url": row["source_url"],
        "source_title": row["source_title"] or "",
        "extracted_at": row["extracted_at"] or "",
        "confidence": row["confidence"],
    }


def claim_site_analysis(user_id: str) -> tuple[str, dict] | str | None:
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    cooldown_from = (now - SITE_SCAN_COOLDOWN).isoformat()
    stale_from = (now - SITE_SCAN_STALE_AFTER).isoformat()
    run_id = secrets.token_hex(16)
    with connect_app() as connection:
        connection.execute("BEGIN IMMEDIATE")
        profile_row = connection.execute(
            "SELECT id, profile_json FROM business_profiles WHERE owner_id = ?",
            (user_id,),
        ).fetchone()
        if not profile_row:
            return None
        running = connection.execute(
            """
            SELECT id FROM profile_site_analysis_runs
            WHERE profile_id = ? AND status = 'running' AND started_at > ?
            ORDER BY started_at DESC LIMIT 1
            """,
            (profile_row["id"], stale_from),
        ).fetchone()
        if running:
            return "busy"
        connection.execute(
            "UPDATE profile_site_analysis_runs SET status = 'failed', finished_at = ?, message = 'stale run' "
            "WHERE profile_id = ? AND status = 'running' AND started_at <= ?",
            (now_text, profile_row["id"], stale_from),
        )
        recent = connection.execute(
            """
            SELECT started_at FROM profile_site_analysis_runs
            WHERE profile_id = ? AND started_at > ?
            ORDER BY started_at DESC LIMIT 1
            """,
            (profile_row["id"], cooldown_from),
        ).fetchone()
        if recent:
            return "cooldown"
        connection.execute(
            "INSERT INTO profile_site_analysis_runs (id, profile_id, started_at, status) VALUES (?, ?, ?, 'running')",
            (run_id, profile_row["id"], now_text),
        )
        try:
            profile = json.loads(profile_row["profile_json"])
        except (TypeError, json.JSONDecodeError):
            profile = {}
        return run_id, profile


def finish_site_analysis(
    run_id: str,
    *,
    status: str,
    pages_scanned: int = 0,
    model: str = "",
    provider: str = "",
    message: str = "",
) -> None:
    if status not in {"completed", "failed"}:
        raise ValueError("Invalid site analysis status")
    with connect_app() as connection:
        connection.execute(
            """
            UPDATE profile_site_analysis_runs
            SET status = ?, finished_at = ?, pages_scanned = ?, model = ?, provider = ?, message = ?
            WHERE id = ? AND status = 'running'
            """,
            (status, now_utc(), pages_scanned, model[:200], provider[:40], message[:500], run_id),
        )


def rate_limit_keys(ip_address: str, login: str) -> list[str]:
    values = (f"ip:{ip_address}", f"login:{login}")
    return [hashlib.sha256(value.encode("utf-8")).hexdigest() for value in values]


def rate_limited(keys: list[str]) -> bool:
    now = datetime.now(timezone.utc)
    cutoff = (now - RATE_WINDOW).isoformat()
    placeholders = ",".join("?" for _ in keys)
    with connect_app() as connection:
        rows = connection.execute(
            f"SELECT attempts FROM auth_rate_limits WHERE key_hash IN ({placeholders}) AND window_started_at > ?",
            (*keys, cutoff),
        ).fetchall()
    return any(int(row["attempts"]) >= RATE_LIMIT for row in rows)


def record_auth_attempt(keys: list[str], *, success: bool) -> None:
    now = datetime.now(timezone.utc)
    cutoff = (now - RATE_WINDOW).isoformat()
    with connect_app() as connection:
        if success:
            placeholders = ",".join("?" for _ in keys)
            connection.execute(f"DELETE FROM auth_rate_limits WHERE key_hash IN ({placeholders})", keys)
            return
        for key in keys:
            connection.execute(
                """
                INSERT INTO auth_rate_limits (key_hash, window_started_at, attempts) VALUES (?, ?, 1)
                ON CONFLICT(key_hash) DO UPDATE SET
                    window_started_at = CASE
                        WHEN auth_rate_limits.window_started_at <= ? THEN excluded.window_started_at
                        ELSE auth_rate_limits.window_started_at
                    END,
                    attempts = CASE
                        WHEN auth_rate_limits.window_started_at <= ? THEN 1
                        ELSE auth_rate_limits.attempts + 1
                    END
                """,
                (key, now.isoformat(), cutoff, cutoff),
            )
