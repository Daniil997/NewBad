import asyncio
import os
import re
import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, SecretStr, field_validator

from app.agent.chat import ChatReply, answer_from_card
from app.agent.generate import analyze_profile_news, answer_profile_chat
from app.agent.loop import build_card
from app.agent.pasted import remember_pasted
from app.corpus.search import (
    CorpusError,
    SearchHit,
    load_bank_documents,
    load_news,
    search_bank_analytics,
    search_news,
)
from app.db import ROOT, connect, init_db, journal_is_empty, journal_tables
from app.ingest.news import import_news_catalog, load_news_sources, migrate_legacy_imports
from app.news_store import prune_stored_news, stored_news_count
from app.market_data import collect_market_data, load_market_config
from app.market_store import latest_market_collection, market_history, market_snapshot, persist_profile_market_links, prune_market_data, record_market_collection, store_observations
from app.personal_feed import (
    article_hash,
    analysis_results,
    claim_job,
    enqueue_job,
    fail_job,
    job_profile,
    missing_candidates,
    save_job_results,
)
from app.user_store import (
    business_profile_for_owner,
    claim_site_analysis,
    create_session,
    create_user,
    connect_app,
    developer_settings,
    decide_profile_suggestion,
    finish_site_analysis,
    init_app_db,
    latest_news_collection,
    pending_profile_suggestions,
    prune_insight_snapshots,
    rate_limit_keys,
    rate_limited,
    record_auth_attempt,
    revoke_session,
    save_business_profile,
    save_developer_settings,
    record_news_collection,
    save_profile_suggestions,
    user_by_login,
    user_for_session,
    now_utc,
)
from app.profile.build import ClientProfile, ClientSummary, get_profile, load_profiles
from app.profile.real_taxonomy import REAL_PROFILE_TAXONOMY
from app.profile.site_crawler import SiteFetchError, crawl_site
from app.profile.site_extract import SiteExtractionError, extract_profile_suggestions
from app.schemas.card import Card
from app.scoring.feed import Feed, build_feed
from app.llm.placement import decide, placement_label
from app.llm.router import current_route, known_modes, local_status, resolve_route
from app.agent.tools import find_news
from app.tracing.journal import latest_steps, read_trace

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
_QUERY_LIMIT = 300
_SESSION_COOKIE = "newbad_session"
_CSRF_COOKIE = "newbad_csrf"
_LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_PASSWORD_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    init_app_db()
    prune_insight_snapshots(developer_settings()["insight_retention_days"])
    scheduler = asyncio.create_task(_news_scheduler())
    market_scheduler = asyncio.create_task(_market_scheduler())
    analysis_worker = asyncio.create_task(_analysis_worker())
    insight_retention_worker = asyncio.create_task(_insight_retention_scheduler())
    try:
        yield
    finally:
        scheduler.cancel()
        market_scheduler.cancel()
        analysis_worker.cancel()
        insight_retention_worker.cancel()
        try:
            await scheduler
        except asyncio.CancelledError:
            pass
        try:
            await insight_retention_worker
        except asyncio.CancelledError:
            pass
        try:
            await market_scheduler
        except asyncio.CancelledError:
            pass
        try:
            await analysis_worker
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Что важно для вашей компании", lifespan=lifespan)


def _check_id(value: str, what: str) -> None:
    if _ID.fullmatch(value) is None:
        raise HTTPException(status_code=400, detail=f"Некорректный идентификатор {what}")


@app.exception_handler(RequestValidationError)
def invalid_parameters(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Некорректные параметры", "fields": fields})


@app.exception_handler(CorpusError)
def corpus_unavailable(_request: Request, exc: CorpusError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundError)
def data_unavailable(_request: Request, _exc: FileNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Данные компаний недоступны"})


@app.get("/api/clients")
def clients() -> list[ClientSummary]:
    return [
        ClientSummary(
            client_id=profile.client_id,
            title=profile.title,
            money_role=profile.money_role,
            exposure_ids=[item.id for item in profile.exposures],
        )
        for profile in load_profiles()
    ]


@app.get("/api/clients/{client_id}/feed")
def client_feed(client_id: str) -> Feed:
    _check_id(client_id, "компании")
    feed = build_feed(client_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    return feed


class PasteNews(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=8000)
    url: str = Field(default="", max_length=2000)
    published_at: str = Field(default="", max_length=40)
    source: str = Field(default="", max_length=200)


class ChatQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ProfileChatStart(BaseModel):
    news_id: str | None = Field(default=None, max_length=120)


class ProfileChatMessage(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ProfileNewsReportRequest(BaseModel):
    refresh: bool = False


class Feedback(BaseModel):
    helpful: bool


class Credentials(BaseModel):
    login: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=10, max_length=128)


class AuthUser(BaseModel):
    id: str
    login: str
    created_at: str


class AuthState(BaseModel):
    authenticated: bool
    user: AuthUser | None = None


InterestState = Literal["interested", "not_relevant", "unknown"]


class BusinessProfileInput(BaseModel):
    model_config = {"extra": "forbid"}

    company_name: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=500)
    country: str = Field(default="", max_length=100)
    region: str = Field(default="", max_length=150)
    city: str = Field(default="", max_length=150)
    description: str = Field(default="", max_length=2000)
    description_skipped: bool = False
    industry: str = Field(default="", max_length=200)
    okved: str = Field(default="", max_length=30)
    industry_tags: list[str] = Field(default_factory=list, max_length=20)
    products_services: str = Field(default="", max_length=1500)
    customer_types: str = Field(default="", max_length=500)
    business_model: str = Field(default="", max_length=500)
    size_band: str = Field(default="", max_length=100)
    business_stage: str = Field(default="", max_length=100)
    credit_interest: InterestState = "unknown"
    deposit_interest: InterestState = "unknown"
    currency_interest: InterestState = "unknown"
    export_import_interest: InterestState = "unknown"
    leasing_interest: InterestState = "unknown"
    guarantees_interest: InterestState = "unknown"
    factoring_interest: InterestState = "unknown"
    settlements_interest: InterestState = "unknown"
    market_geographies: list[str] = Field(default_factory=list, max_length=20)
    dependencies: str = Field(default="", max_length=1000)
    focus_topics: list[str] = Field(default_factory=list, max_length=30)
    external_factors: str = Field(default="", max_length=1500)
    context_note: str = Field(default="", max_length=1500)
    language: Literal["ru", "en"] = "ru"

    @field_validator("industry_tags", "market_geographies", "focus_topics")
    @classmethod
    def normalize_terms(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            term = value.strip()
            if len(term) > 120:
                raise ValueError("Каждый тег должен быть не длиннее 120 символов")
            key = term.casefold()
            if term and key not in seen:
                result.append(term)
                seen.add(key)
        return result

    @field_validator("industry_tags")
    @classmethod
    def validate_industry_tags(cls, values: list[str]) -> list[str]:
        allowed = {item["id"] for item in REAL_PROFILE_TAXONOMY["industry_tags"]}
        if unknown := set(values) - allowed:
            raise ValueError(f"Неизвестные отраслевые теги: {', '.join(sorted(unknown))}")
        return values

    @field_validator("focus_topics")
    @classmethod
    def validate_focus_topics(cls, values: list[str]) -> list[str]:
        allowed = {item["id"] for item in REAL_PROFILE_TAXONOMY["focus_topics"]}
        if unknown := set(values) - allowed:
            raise ValueError(f"Неизвестные темы профиля: {', '.join(sorted(unknown))}")
        return values


class BusinessProfileView(BusinessProfileInput):
    profile_version: int
    status: Literal["incomplete", "complete"]
    updated_at: str


class SiteAnalysisRequest(BaseModel):
    consent: bool


class ProfileSuggestionDecision(BaseModel):
    field_name: str = Field(min_length=1, max_length=40)
    source_url: str = Field(min_length=1, max_length=2000)
    accept: bool


class SettingsView(BaseModel):
    mode: str
    allow_cloud: bool
    active_generation: str
    model: str
    weights_from_browser: bool
    local_server: str
    placement: str
    placement_choice: str
    news_lookback_days: int
    news_retention_days: int
    news_max_items: int
    news_collection_interval_hours: int
    market_retention_days: int
    market_max_items_per_instrument: int
    market_collection_interval_hours: int
    market_categories: list[str]
    personal_analysis_trigger: str
    personal_analysis_interval_hours: int
    personal_analysis_max_candidates: int
    insight_refresh_mode: str
    insight_refresh_interval_hours: int
    insight_retention_days: int
    task_routes: dict[str, dict[str, str]]


class SettingsUpdate(BaseModel):
    mode: str = Field(min_length=1, max_length=40)
    allow_cloud: bool
    placement: str = Field(min_length=1, max_length=20)
    news_lookback_days: int = Field(default=3, ge=1, le=30)
    news_retention_days: int = Field(default=90, ge=1, le=3650)
    news_max_items: int = Field(default=500, ge=10, le=10000)
    news_collection_interval_hours: int = Field(default=0, ge=0, le=168)
    market_retention_days: int = Field(default=90, ge=1, le=3650)
    market_max_items_per_instrument: int = Field(default=1000, ge=10, le=10000)
    market_collection_interval_hours: int = Field(default=0, ge=0, le=168)
    market_categories: list[Literal["currencies", "equities", "metals"]] = Field(
        default_factory=lambda: ["currencies", "equities", "metals"], min_length=1, max_length=3
    )
    personal_analysis_trigger: Literal["first_then_manual", "on_new_interval", "manual"] = "first_then_manual"
    personal_analysis_interval_hours: int = Field(default=24, ge=1, le=168)
    personal_analysis_max_candidates: int = Field(default=30, ge=1, le=100)
    insight_refresh_mode: Literal["manual", "interval"] = "manual"
    insight_refresh_interval_hours: int = Field(default=24, ge=1, le=168)
    insight_retention_days: int = Field(default=90, ge=1, le=3650)
    task_routes: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator("market_categories")
    @classmethod
    def unique_market_categories(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Категории рыночных данных не должны повторяться")
        return values

    @field_validator("task_routes")
    @classmethod
    def valid_task_routes(cls, values: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        expected = {"real": {"profile_site", "news_report", "chat"}, "demo": {"news_card", "chat"}}
        allowed = {"inherit", "mock", "local_gemma", "local_qwen", "cloud_gemma", "cloud_glm", "cloud_qwen", "cloud_deepseek", "cloud_vl", "cloud_kimi"}
        if not values:
            return values
        if set(values) - set(expected):
            raise ValueError("Неизвестное пространство маршрутизации модели")
        for space, routes in values.items():
            if set(routes) - expected[space] or any(route not in allowed for route in routes.values()):
                raise ValueError("Недопустимая задача или модель в маршрутизации")
        return values


_PLACEMENTS = {"auto", "gpu", "ram"}


def _collect_news() -> dict:
    configured = developer_settings()
    output = ROOT / "data" / "news" / "imported"
    migrate_legacy_imports(output)
    run = import_news_catalog(output, max_age_days=configured["news_lookback_days"])
    retention = prune_stored_news(
        retention_days=configured["news_retention_days"],
        max_items=configured["news_max_items"],
    )
    result = run.model_dump()
    result["retention"] = retention
    record_news_collection(result)
    return result


def _collect_market() -> dict:
    configured = developer_settings()
    run = collect_market_data(configured["market_categories"])
    added = store_observations(run["items"])
    retention = prune_market_data(
        retention_days=configured["market_retention_days"],
        max_items_per_instrument=configured["market_max_items_per_instrument"],
    )
    result = {key: value for key, value in run.items() if key != "items"}
    result["stored"] = added
    result["retention"] = retention
    record_market_collection(result)
    return result


async def _news_scheduler() -> None:
    interval_anchor = datetime.now(timezone.utc)
    previous_interval = 0
    while True:
        await asyncio.sleep(60)
        interval = developer_settings()["news_collection_interval_hours"]
        now = datetime.now(timezone.utc)
        if interval != previous_interval:
            previous_interval = interval
            interval_anchor = now
        if not interval:
            continue
        latest = latest_news_collection()
        if latest:
            try:
                interval_anchor = datetime.fromisoformat(latest["finished_at"])
            except ValueError:
                pass
        if (now - interval_anchor).total_seconds() < interval * 3600:
            continue
        interval_anchor = now
        try:
            await asyncio.to_thread(_collect_news)
        except Exception:
            # Status of source-level failures is recorded by the importer; the next interval retries.
            continue


async def _market_scheduler() -> None:
    interval_anchor = datetime.now(timezone.utc)
    previous_interval = 0
    while True:
        await asyncio.sleep(60)
        interval = developer_settings()["market_collection_interval_hours"]
        now = datetime.now(timezone.utc)
        if interval != previous_interval:
            previous_interval = interval
            interval_anchor = now
        if not interval:
            continue
        latest = latest_market_collection()
        if latest:
            try:
                interval_anchor = datetime.fromisoformat(latest["finished_at"])
            except ValueError:
                pass
        if (now - interval_anchor).total_seconds() < interval * 3600:
            continue
        interval_anchor = now
        try:
            await asyncio.to_thread(_collect_market)
        except Exception:
            continue


async def _insight_retention_scheduler() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            settings = developer_settings()
            await asyncio.to_thread(prune_insight_snapshots, settings["insight_retention_days"])
        except Exception:
            continue


async def _analysis_worker() -> None:
    while True:
        job = await asyncio.to_thread(claim_job)
        if not job:
            await asyncio.sleep(2)
            continue
        try:
            profile = await asyncio.to_thread(job_profile, job)
            if profile is None:
                await asyncio.to_thread(fail_job, job["id"], "stale_profile_version")
                continue
            imported = await asyncio.to_thread(load_news)
            by_id = {item.id: item for item in imported if item.status == "imported"}
            await asyncio.to_thread(save_job_results, job, profile, by_id)
        except Exception as error:
            await asyncio.to_thread(fail_job, job["id"], error.__class__.__name__)


@app.get("/api/auth/csrf")
def auth_csrf(response: Response) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        _CSRF_COOKIE,
        token,
        max_age=24 * 60 * 60,
        httponly=False,
        secure=_secure_cookies(),
        samesite="lax",
        path="/",
    )
    return {"csrf_token": token}


@app.post("/api/auth/register", status_code=201)
def auth_register(body: Credentials, request: Request, response: Response) -> dict:
    _check_csrf(request)
    if not _registration_enabled():
        raise HTTPException(status_code=403, detail="Регистрация временно отключена")
    login = _canonical_login(body.login)
    keys = _auth_rate_keys(request, login)
    _check_auth_rate(keys)
    try:
        user = create_user(login, _hash_password(body.password.get_secret_value()))
    except sqlite3.IntegrityError as exc:
        record_auth_attempt(keys, success=False)
        raise HTTPException(status_code=409, detail="Не удалось создать аккаунт с этим логином") from exc
    record_auth_attempt(keys, success=True)
    _start_session(user["id"], response)
    return user


@app.post("/api/auth/login")
def auth_login(body: Credentials, request: Request, response: Response) -> dict:
    _check_csrf(request)
    login = _canonical_login(body.login)
    keys = _auth_rate_keys(request, login)
    _check_auth_rate(keys)
    user = user_by_login(login)
    password = body.password.get_secret_value()
    verified = _verify_password(password, user["password_hash"] if user else None)
    if not user or user["status"] != "active" or not verified:
        record_auth_attempt(keys, success=False)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    record_auth_attempt(keys, success=True)
    _start_session(user["id"], response)
    return {"id": user["id"], "login": user["login"], "created_at": user["created_at"]}


@app.get("/api/auth/me", response_model=AuthState)
def auth_me(request: Request) -> AuthState:
    token = request.cookies.get(_SESSION_COOKIE, "")
    user = user_for_session(token) if token else None
    if not user:
        return AuthState(authenticated=False)
    return AuthState(
        authenticated=True,
        user=AuthUser(id=user["id"], login=user["login"], created_at=user["created_at"]),
    )


def _require_user(request: Request) -> dict:
    token = request.cookies.get(_SESSION_COOKIE, "")
    user = user_for_session(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Войдите в аккаунт")
    return user


def _profile_view(saved: dict) -> BusinessProfileView:
    return BusinessProfileView(
        **saved["profile"],
        profile_version=saved["profile_version"],
        status=saved["status"],
        updated_at=saved["updated_at"],
    )


@app.get("/api/real/profile")
def real_profile(request: Request) -> dict:
    user = _require_user(request)
    saved = business_profile_for_owner(user["id"])
    if not saved:
        raise HTTPException(status_code=404, detail="Профиль компании не найден")
    return {"profile": _profile_view(saved)}


def _fresh_real_news(days: int) -> list:
    cutoff = date.today() - timedelta(days=max(days - 1, 0))
    result = []
    for item in load_news():
        if item.status != "imported":
            continue
        try:
            published = date.fromisoformat(item.published_at)
        except ValueError:
            continue
        if published >= cutoff:
            result.append(item)
    return sorted(result, key=lambda item: (item.published_at, item.id), reverse=True)


def _cached_company_insight(saved: dict, news_rows: list[dict], *, refresh: bool = False) -> dict:
    profile = saved["profile"]
    market_payload = market_snapshot()
    market_signals = []
    profile_links = persist_profile_market_links(
        saved["profile_id"], saved["profile_version"], profile, load_market_config()["categories"],
    )
    link_reasons = {(item["category"], item["instrument"]): item["reason"] for item in profile_links}
    for item in market_payload.get("items", []):
        reason = link_reasons.get((item["category"], item["instrument"]), "")
        if reason:
            market_signals.append({
                "category": item["category"], "instrument": item["instrument"], "title": item["name"],
                "price": item["price"], "change_pct": item["change_pct"], "currency": item["currency"],
                "source_url": item["source_url"], "source_timestamp": item["source_timestamp"], "reason": reason,
            })
    payload = {"news": news_rows[:3], "markets": market_signals[:3]}
    input_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    settings = developer_settings()
    prune_insight_snapshots(settings["insight_retention_days"])
    now = datetime.now(timezone.utc)
    with connect_app() as connection:
        latest = connection.execute(
            "SELECT id, input_hash, payload_json, created_at FROM profile_insight_snapshots "
            "WHERE profile_id = ? AND profile_version = ? ORDER BY created_at DESC LIMIT 1",
            (saved["profile_id"], saved["profile_version"]),
        ).fetchone()
        exact = connection.execute(
            "SELECT id, input_hash, payload_json, created_at FROM profile_insight_snapshots "
            "WHERE profile_id = ? AND profile_version = ? AND input_hash = ?",
            (saved["profile_id"], saved["profile_version"], input_hash),
        ).fetchone()
        due = latest is None
        if latest is not None:
            try:
                due = now - datetime.fromisoformat(latest["created_at"]) >= timedelta(hours=settings["insight_refresh_interval_hours"])
            except (TypeError, ValueError):
                due = True
        should_save = refresh or (settings["insight_refresh_mode"] == "interval" and due)
        if should_save:
            connection.execute(
                "INSERT INTO profile_insight_snapshots (profile_id, profile_version, input_hash, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(profile_id, profile_version, input_hash) "
                "DO UPDATE SET payload_json = excluded.payload_json, created_at = excluded.created_at",
                (saved["profile_id"], saved["profile_version"], input_hash,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True), now.isoformat()),
            )
            connection.execute(
                "DELETE FROM profile_insight_snapshots WHERE profile_id = ? AND id NOT IN "
                "(SELECT id FROM profile_insight_snapshots WHERE profile_id = ? ORDER BY created_at DESC LIMIT 200)",
                (saved["profile_id"], saved["profile_id"]),
            )
            latest = connection.execute(
                "SELECT id, input_hash, payload_json, created_at FROM profile_insight_snapshots "
                "WHERE profile_id = ? AND profile_version = ? ORDER BY created_at DESC LIMIT 1",
                (saved["profile_id"], saved["profile_version"]),
            ).fetchone()
        elif exact is not None and (latest is None or exact["id"] >= latest["id"]):
            latest = exact
    if latest is None:
        return {"news": [], "markets": [], "updated_at": None, "stale": bool(news_rows or market_signals),
                "status": "Обзор пока не сохранён — обновите вручную", "refresh_mode": settings["insight_refresh_mode"]}
    try:
        snapshot = json.loads(latest["payload_json"])
    except (TypeError, json.JSONDecodeError):
        snapshot = {"news": [], "markets": []}
    stale = latest["input_hash"] != input_hash
    status = "Обзор актуален"
    if stale and settings["insight_refresh_mode"] == "manual":
        status = "Появились новые данные — обновите обзор вручную"
    elif stale:
        status = "Есть новые данные; обзор обновится по расписанию"
    return {**snapshot, "updated_at": latest["created_at"], "stale": stale, "status": status,
            "refresh_mode": settings["insight_refresh_mode"]}


def _real_feed_payload(user_id: str, *, enqueue_manual: bool = False, refresh_insight: bool = False, skip_auto_queue: bool = False) -> dict:
    saved = business_profile_for_owner(user_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Профиль компании не найден")
    profile_id = saved["profile_id"]
    profile_version = saved["profile_version"]
    profile_complete = saved["status"] == "complete"
    settings = developer_settings()
    news_items = _fresh_real_news(settings["news_lookback_days"])
    state = analysis_results(profile_id, profile_version)
    missing = missing_candidates(profile_id, profile_version, news_items)
    latest_job = state["job"]
    current_analysis_count = sum(
        1 for item in news_items
        if (saved_analysis := state["items"].get(item.id)) is not None
        and saved_analysis["news_hash"] == article_hash(item)
    )
    should_enqueue = enqueue_manual
    trigger = settings["personal_analysis_trigger"]
    if not skip_auto_queue and not enqueue_manual and missing and trigger == "first_then_manual" and current_analysis_count == 0 and latest_job is None:
        should_enqueue = True
    if not skip_auto_queue and not enqueue_manual and missing and trigger == "on_new_interval":
        due = latest_job is None
        if latest_job:
            try:
                updated = datetime.fromisoformat(latest_job["updated_at"])
                due = datetime.now(timezone.utc) - updated >= timedelta(hours=settings["personal_analysis_interval_hours"])
            except (TypeError, ValueError):
                due = False
        should_enqueue = due
    if not skip_auto_queue and trigger == "manual" and not enqueue_manual:
        should_enqueue = False
    if not profile_complete:
        should_enqueue = False
    if should_enqueue and missing:
        enqueue_job(profile_id, profile_version, missing[:settings["personal_analysis_max_candidates"]])
        state = analysis_results(profile_id, profile_version)
        missing = missing_candidates(profile_id, profile_version, news_items)
    rows = []
    with connect_app() as connection:
        saved_reports = connection.execute(
            "SELECT news_id, news_hash, report_json, created_at FROM profile_news_reports "
            "WHERE profile_id = ? AND profile_version = ? AND report_version = 'profile-news-report-v1'",
            (profile_id, profile_version),
        ).fetchall()
    reports_by_id = {row["news_id"]: row for row in saved_reports}
    for item in news_items:
        analysis = state["items"].get(item.id)
        if analysis and analysis["news_hash"] != article_hash(item):
            analysis = None
        report_row = reports_by_id.get(item.id)
        report = None
        if report_row and report_row["news_hash"] == article_hash(item):
            try:
                report_data = json.loads(report_row["report_json"])
                report = {
                    **report_data, "created_at": report_row["created_at"],
                    "citations": ([{"title": item.title, "url": item.url, "published_at": item.published_at, "kind": "news"}]
                                  if f"news:{item.id}" in report_data.get("evidence_ids", []) else [])
                    + ([{"title": "Профиль компании", "url": "", "published_at": "", "kind": "profile"}]
                       if "company-profile" in report_data.get("evidence_ids", []) else []),
                }
            except (TypeError, json.JSONDecodeError):
                report = None
        rows.append({
            "id": item.id, "title": item.title, "published_at": item.published_at,
            "excerpt": item.excerpt, "event_type": item.event_type or "unknown",
            "event_title": item.event_title or "Новости бизнеса", "source": item.source or "Официальный источник",
            "url": item.url, "analysis": analysis, "report": report,
        })
    rows.sort(key=lambda item: (bool(item["analysis"] and item["analysis"]["relevant"]), item["analysis"]["score"] if item["analysis"] else 0, item["published_at"]), reverse=True)
    relevant = [row for row in rows if row["analysis"] and row["analysis"]["relevant"]]
    insight = _cached_company_insight(saved, relevant, refresh=refresh_insight)
    return {
        "items": rows, "insight": insight["news"], "insight_markets": insight["markets"],
        "insight_updated_at": insight["updated_at"], "insight_status": insight["status"],
        "insight_stale": insight["stale"], "insight_refresh_mode": insight["refresh_mode"],
        "profile_version": profile_version,
        "lookback_days": settings["news_lookback_days"], "analysis_method": "Правила без модели",
        "analyzed_count": current_analysis_count,
        "pending_count": len(missing) + (len(state["job"]["candidate_ids"]) if state["job"] and state["job"]["status"] in {"queued", "running"} else 0),
        "job": state["job"],
        "trigger": trigger,
        "personalization_available": profile_complete,
    }


@app.get("/api/real/feed")
def real_feed(request: Request) -> dict:
    user = _require_user(request)
    return _real_feed_payload(user["id"])


@app.post("/api/real/insight/refresh")
def refresh_real_insight(request: Request) -> dict:
    _check_csrf(request)
    user = _require_user(request)
    return _real_feed_payload(user["id"], refresh_insight=True, skip_auto_queue=True)


@app.post("/api/real/news/{news_id}/report")
def create_profile_news_report(news_id: str, payload: ProfileNewsReportRequest, request: Request) -> dict:
    _check_csrf(request)
    user = _require_user(request)
    saved = business_profile_for_owner(user["id"])
    if not saved or saved["status"] != "complete":
        raise HTTPException(status_code=409, detail="Заполните профиль компании для подробного разбора")
    news = next((item for item in _fresh_real_news(developer_settings()["news_lookback_days"]) if item.id == news_id), None)
    if news is None:
        raise HTTPException(status_code=404, detail="Новость отсутствует в актуальном официальном окне")
    news_hash = article_hash(news)
    report_version = "profile-news-report-v1"
    with connect_app() as connection:
        cached = connection.execute(
            "SELECT report_json, created_at FROM profile_news_reports WHERE profile_id = ? AND profile_version = ? "
            "AND news_id = ? AND news_hash = ? AND report_version = ?",
            (saved["profile_id"], saved["profile_version"], news.id, news_hash, report_version),
        ).fetchone()
    if cached and not payload.refresh:
        report_data = json.loads(cached["report_json"])
        citations = ([{"title": news.title, "url": news.url, "published_at": news.published_at, "kind": "news"}]
                     if f"news:{news.id}" in report_data.get("evidence_ids", []) else [])
        if "company-profile" in report_data.get("evidence_ids", []):
            citations.append({"title": "Профиль компании", "url": "", "published_at": "", "kind": "profile"})
        return {**report_data, "created_at": cached["created_at"], "cached": True, "citations": citations}
    report = analyze_profile_news(saved["profile"], news)
    if report is None:
        raise HTTPException(status_code=503, detail="Модель не смогла сформировать проверяемый разбор. Попробуйте позже или проверьте настройки модели.")
    now = now_utc()
    with connect_app() as connection:
        connection.execute(
            "INSERT INTO profile_news_reports (profile_id, profile_version, news_id, news_hash, report_version, report_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(profile_id, profile_version, news_id, news_hash, report_version) "
            "DO UPDATE SET report_json = excluded.report_json, created_at = excluded.created_at",
            (saved["profile_id"], saved["profile_version"], news.id, news_hash, report_version,
             json.dumps(report, ensure_ascii=False, sort_keys=True), now),
        )
        connection.execute(
            "DELETE FROM profile_news_reports WHERE profile_id = ? AND id NOT IN "
            "(SELECT id FROM profile_news_reports WHERE profile_id = ? ORDER BY created_at DESC LIMIT 500)",
            (saved["profile_id"], saved["profile_id"]),
        )
    citations = ([{"title": news.title, "url": news.url, "published_at": news.published_at, "kind": "news"}]
                 if f"news:{news.id}" in report["evidence_ids"] else [])
    if "company-profile" in report["evidence_ids"]:
        citations.append({"title": "Профиль компании", "url": "", "published_at": "", "kind": "profile"})
    return {**report, "created_at": now, "cached": False, "citations": citations}


def _profile_chat_evidence(saved: dict, focused_news_id: str | None) -> list[dict]:
    profile = saved["profile"]
    fields = {
        "Компания": profile.get("company_name"), "Отрасль": profile.get("industry"),
        "Описание": profile.get("description"), "Товары и услуги": profile.get("products_services"),
        "Клиенты": profile.get("customer_types"), "Модель бизнеса": profile.get("business_model"),
        "Регион": ", ".join(filter(None, [profile.get("country"), profile.get("region"), profile.get("city")])),
        "Размер и этап": ", ".join(filter(None, [profile.get("size_band"), profile.get("business_stage")])),
        "Отраслевые теги": ", ".join(profile.get("industry_tags", [])),
        "Интересы": ", ".join(profile.get("focus_topics", [])),
        "География рынков": ", ".join(profile.get("market_geographies", [])),
        "Зависимости и факторы": "; ".join(filter(None, [profile.get("dependencies"), profile.get("external_factors")])),
        "Контекст компании": profile.get("context_note"),
    }
    profile_text = "\n".join(f"{label}: {value}" for label, value in fields.items() if value)
    evidence = [{
        "evidence_id": "company-profile", "title": "Профиль компании пользователя",
        "text": profile_text or "Профиль компании пока не содержит заполненных сведений.",
        "url": "", "published_at": "", "kind": "profile",
    }]
    articles = _fresh_real_news(developer_settings()["news_lookback_days"])
    selected = next((item for item in articles if item.id == focused_news_id), None)
    chosen = ([selected] if selected else []) + [item for item in articles if item.id != focused_news_id][:5]
    for item in chosen[:6]:
        evidence.append({
            "evidence_id": f"news:{item.id}", "title": item.title,
            "text": f"{item.title}. {item.excerpt}", "url": item.url,
            "published_at": item.published_at, "kind": "news", "source": item.source or "Официальный источник",
            "focus": bool(selected and item.id == selected.id),
        })
    try:
        for index, item in enumerate(market_snapshot().get("items", [])[:5]):
            evidence.append({
                "evidence_id": f"market:{item['category']}:{item['instrument']}:{index}",
                "title": item["name"],
                "text": f"{item['name']}: {item['price']} {item['currency']}; изменение {item['change_pct']}%; время источника {item['source_timestamp']}.",
                "url": item["source_url"], "published_at": item["source_timestamp"], "kind": "market",
                "source": "Московская биржа (MOEX ISS)",
            })
    except (KeyError, TypeError):
        pass
    return evidence[:12]


def _profile_chat_session(owner_id: str, session_id: str):
    with connect_app() as connection:
        row = connection.execute(
            "SELECT id, profile_id, profile_version, title, focused_news_id, created_at, updated_at "
            "FROM profile_chat_sessions WHERE id = ? AND owner_id = ?", (session_id, owner_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return row


@app.get("/api/real/chat/sessions")
def list_profile_chat_sessions(request: Request) -> dict:
    user = _require_user(request)
    with connect_app() as connection:
        rows = connection.execute(
            "SELECT id, title, focused_news_id, created_at, updated_at FROM profile_chat_sessions "
            "WHERE owner_id = ? ORDER BY updated_at DESC LIMIT 50", (user["id"],),
        ).fetchall()
    return {"sessions": [dict(row) for row in rows]}


@app.post("/api/real/chat/sessions")
def create_profile_chat_session(payload: ProfileChatStart, request: Request) -> dict:
    _check_csrf(request)
    user = _require_user(request)
    saved = business_profile_for_owner(user["id"])
    if not saved:
        raise HTTPException(status_code=404, detail="Профиль компании не найден")
    focused = None
    title = "Общий чат по бизнесу"
    if payload.news_id:
        focused = next((item for item in load_news() if item.id == payload.news_id and item.status == "imported"), None)
        if focused is None:
            raise HTTPException(status_code=404, detail="Новость не найдена среди официальных материалов")
        title = focused.title[:180]
    session_id = str(uuid.uuid4())
    now = now_utc()
    with connect_app() as connection:
        connection.execute(
            "INSERT INTO profile_chat_sessions "
            "(id, owner_id, profile_id, profile_version, title, focused_news_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, user["id"], saved["profile_id"], saved["profile_version"], title,
             payload.news_id, now, now),
        )
    return {"id": session_id, "title": title, "focused_news_id": payload.news_id, "messages": []}


@app.get("/api/real/chat/sessions/{session_id}")
def get_profile_chat_session(session_id: str, request: Request) -> dict:
    user = _require_user(request)
    session = _profile_chat_session(user["id"], session_id)
    with connect_app() as connection:
        rows = connection.execute(
            "SELECT role, text, citations_json, created_at FROM profile_chat_messages "
            "WHERE session_id = ? ORDER BY id", (session_id,),
        ).fetchall()
    messages = []
    for row in rows:
        try:
            citations = json.loads(row["citations_json"])
        except (TypeError, json.JSONDecodeError):
            citations = []
        messages.append({"role": row["role"], "text": row["text"], "citations": citations, "created_at": row["created_at"]})
    return {**dict(session), "messages": messages}


@app.post("/api/real/chat/sessions/{session_id}/messages")
def send_profile_chat_message(session_id: str, payload: ProfileChatMessage, request: Request) -> dict:
    _check_csrf(request)
    user = _require_user(request)
    session = _profile_chat_session(user["id"], session_id)
    saved = business_profile_for_owner(user["id"])
    if not saved or saved["profile_id"] != session["profile_id"]:
        raise HTTPException(status_code=409, detail="Профиль компании недоступен")
    evidence = _profile_chat_evidence(saved, session["focused_news_id"])
    with connect_app() as connection:
        rows = connection.execute(
            "SELECT role, text FROM profile_chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT 8",
            (session_id,),
        ).fetchall()
    history = [{"role": row["role"], "content": row["text"]} for row in reversed(rows)]
    result = answer_profile_chat(payload.question.strip(), history, evidence)
    if result is None:
        raise HTTPException(status_code=503, detail="Модель не смогла подготовить ответ по подтверждённым материалам. Попробуйте переформулировать вопрос или проверить доступность модели.")
    by_id = {item["evidence_id"]: item for item in evidence}
    citations = [
        {"title": by_id[item_id]["title"], "url": by_id[item_id]["url"],
         "published_at": by_id[item_id]["published_at"], "kind": by_id[item_id]["kind"]}
        for item_id in result["evidence_ids"]
    ]
    now = now_utc()
    with connect_app() as connection:
        connection.execute(
            "INSERT INTO profile_chat_messages (session_id, role, text, citations_json, created_at) VALUES (?, 'user', ?, '[]', ?)",
            (session_id, payload.question.strip(), now),
        )
        connection.execute(
            "INSERT INTO profile_chat_messages (session_id, role, text, citations_json, created_at) VALUES (?, 'assistant', ?, ?, ?)",
            (session_id, result["answer"], json.dumps(citations, ensure_ascii=False), now),
        )
        connection.execute(
            "UPDATE profile_chat_sessions SET profile_version = ?, updated_at = ? WHERE id = ?",
            (saved["profile_version"], now, session_id),
        )
    return {"role": "assistant", "text": result["answer"], "citations": citations, "created_at": now}


@app.post("/api/real/feed/analyze")
def analyze_real_feed(request: Request) -> dict:
    _check_csrf(request)
    user = _require_user(request)
    saved = business_profile_for_owner(user["id"])
    if not saved or saved["status"] != "complete":
        raise HTTPException(status_code=409, detail="Заполните профиль компании, чтобы запустить персональный анализ")
    return _real_feed_payload(user["id"], enqueue_manual=True)


@app.get("/api/real/markets")
def real_markets(request: Request) -> dict:
    user = _require_user(request)
    saved = business_profile_for_owner(user["id"])
    profile = saved["profile"] if saved else {}
    snapshot = market_snapshot()
    links = persist_profile_market_links(
        saved["profile_id"], saved["profile_version"], profile, load_market_config()["categories"],
    ) if saved else []
    link_reasons = {(item["category"], item["instrument"]): item["reason"] for item in links}
    for item in snapshot["items"]:
        item["profile_reason"] = link_reasons.get((item["category"], item["instrument"]), "")
    snapshot["items"].sort(key=lambda item: (not bool(item["profile_reason"]), item["category"], item["instrument"]))
    return {
        "provider": "MOEX ISS", "delayed": True, "snapshot": snapshot, "profile_market_links": links,
        "latest": latest_market_collection(),
    }


@app.get("/api/real/markets/{category}/{instrument}")
def real_market_history(category: str, instrument: str, request: Request) -> dict:
    _require_user(request)
    _check_id(instrument, "инструмента")
    catalog = load_market_config()["categories"]
    if category not in catalog or instrument not in catalog[category]["instruments"]:
        raise HTTPException(status_code=404, detail="Инструмент не найден в каталоге MOEX")
    return {"category": category, "instrument": instrument, **market_history(category, instrument)}


@app.get("/api/real/profile/options")
def real_profile_options(request: Request) -> dict:
    _require_user(request)
    return REAL_PROFILE_TAXONOMY


@app.get("/api/real/profile/suggestions")
def real_profile_suggestions(request: Request) -> dict:
    user = _require_user(request)
    return {"suggestions": pending_profile_suggestions(user["id"])}


@app.post("/api/real/profile/site-analysis")
def analyze_real_profile_site(body: SiteAnalysisRequest, request: Request) -> dict:
    _check_csrf(request)
    if not body.consent:
        raise HTTPException(status_code=400, detail="Подтвердите отправку текста страниц выбранной модели")
    user = _require_user(request)
    saved = business_profile_for_owner(user["id"])
    if not saved:
        raise HTTPException(status_code=404, detail="Профиль компании не найден")
    claim = claim_site_analysis(user["id"])
    if claim is None:
        raise HTTPException(status_code=404, detail="Профиль компании не найден")
    if claim == "busy":
        raise HTTPException(status_code=409, detail="Анализ сайта уже выполняется")
    if claim == "cooldown":
        raise HTTPException(status_code=429, detail="Повторный анализ будет доступен через несколько минут")
    run_id, profile = claim
    website = str(profile.get("website") or "").strip()
    if not website:
        finish_site_analysis(run_id, status="failed", message="website_missing")
        raise HTTPException(status_code=400, detail="Сначала сохраните адрес сайта в профиле")
    try:
        route = resolve_route("heavy", task="profile_site", space="real")
    except Exception as exc:
        finish_site_analysis(run_id, status="failed", message="route_resolution_failed")
        raise HTTPException(status_code=503, detail="Не удалось подготовить модель анализа") from exc
    if route.provider in {"mock", "replay"}:
        finish_site_analysis(run_id, status="failed", message="model_unavailable")
        raise HTTPException(status_code=503, detail="Для анализа сайта настройте доступную модель")
    try:
        crawl = crawl_site(website)
    except SiteFetchError as exc:
        finish_site_analysis(run_id, status="failed", message="site_fetch_rejected")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        finish_site_analysis(run_id, status="failed", message="site_fetch_failed")
        raise HTTPException(status_code=502, detail="Не удалось безопасно прочитать сайт") from exc
    if not crawl.pages:
        finish_site_analysis(
            run_id, status="completed", pages_scanned=0, model=route.model,
            provider=route.provider, message="no_readable_pages",
        )
        return {
            "status": "no_pages",
            "suggestions": pending_profile_suggestions(user["id"]),
            "warnings": crawl.warnings,
            "pages_scanned": 0,
        }
    try:
        extracted = extract_profile_suggestions(crawl.pages, profile)
    except SiteExtractionError as exc:
        finish_site_analysis(
            run_id, status="failed", pages_scanned=len(crawl.pages), model=route.model,
            provider=route.provider, message="extraction_failed",
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        finish_site_analysis(
            run_id, status="failed", pages_scanned=len(crawl.pages), model=route.model,
            provider=route.provider, message="extraction_failed",
        )
        raise HTTPException(status_code=502, detail="Не удалось завершить анализ сайта") from exc
    save_profile_suggestions(user["id"], extracted.suggestions)
    saved, filled_fields = _apply_pending_suggestions(user["id"])
    finish_site_analysis(
        run_id, status="completed", pages_scanned=len(crawl.pages),
        model=extracted.model, provider=extracted.provider,
    )
    warnings = [*crawl.warnings]
    if extracted.warning:
        warnings.append(extracted.warning)
    return {
        "status": "applied" if filled_fields else "no_suggestions",
        "profile": _profile_view(saved) if saved else None,
        "filled_fields": filled_fields,
        "suggestions": pending_profile_suggestions(user["id"]),
        "warnings": warnings,
        "pages_scanned": len(crawl.pages),
        "opened_url": getattr(crawl, "opened_url", ""),
        "model": extracted.model,
        "provider": extracted.provider,
    }


def _apply_pending_suggestions(user_id: str) -> tuple[dict | None, list[str]]:
    filled_fields: list[str] = []
    for item in pending_profile_suggestions(user_id):
        current = business_profile_for_owner(user_id)
        field_name = item["field_name"]
        source_url = item["source_url"]
        if not current:
            break
        try:
            validated = BusinessProfileInput.model_validate(
                {**current["profile"], field_name: item["value"]}
            )
        except Exception:
            decide_profile_suggestion(user_id, field_name, source_url, accept=False)
            continue
        if getattr(validated, field_name) != item["value"]:
            decide_profile_suggestion(user_id, field_name, source_url, accept=False)
            continue
        if decide_profile_suggestion(user_id, field_name, source_url, accept=True) == "accepted":
            filled_fields.append(field_name)
    return business_profile_for_owner(user_id), filled_fields


@app.post("/api/real/profile/suggestions/apply")
def apply_real_profile_suggestions(request: Request) -> dict:
    _check_csrf(request)
    user = _require_user(request)
    saved, filled_fields = _apply_pending_suggestions(user["id"])
    if not saved:
        raise HTTPException(status_code=404, detail="Профиль компании не найден")
    return {
        "profile": _profile_view(saved),
        "filled_fields": filled_fields,
        "suggestions": pending_profile_suggestions(user["id"]),
    }


@app.post("/api/real/profile/suggestions/decision")
def decide_real_profile_suggestion(body: ProfileSuggestionDecision, request: Request) -> dict:
    _check_csrf(request)
    user = _require_user(request)
    if body.accept:
        candidates = pending_profile_suggestions(user["id"])
        candidate = next(
            (
                item for item in candidates
                if item["field_name"] == body.field_name and item["source_url"] == body.source_url
            ),
            None,
        )
        if not candidate:
            raise HTTPException(status_code=404, detail="Предложение больше недоступно")
        current = business_profile_for_owner(user["id"])
        if not current:
            raise HTTPException(status_code=404, detail="Профиль компании не найден")
        try:
            validated = BusinessProfileInput.model_validate(
                {**current["profile"], body.field_name: candidate["value"]}
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Предложенное значение не прошло проверку") from exc
        if getattr(validated, body.field_name) != candidate["value"]:
            raise HTTPException(status_code=422, detail="Предложенное значение не прошло проверку")
    result = decide_profile_suggestion(
        user["id"], body.field_name, body.source_url, accept=body.accept
    )
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Предложение больше недоступно")
    if result == "conflict":
        raise HTTPException(
            status_code=409,
            detail="Поле уже заполнено вручную; оно не было перезаписано. Обновите список предложений.",
        )
    saved = business_profile_for_owner(user["id"])
    return {
        "status": result,
        "profile": _profile_view(saved) if saved else None,
        "suggestions": pending_profile_suggestions(user["id"]),
    }


@app.put("/api/real/profile")
def update_real_profile(body: BusinessProfileInput, request: Request) -> dict:
    _check_csrf(request)
    user = _require_user(request)
    profile = body.model_dump()
    for key, value in profile.items():
        if isinstance(value, str):
            profile[key] = value.strip()
        elif isinstance(value, list):
            profile[key] = [item.strip() for item in value if item.strip()]

    website = profile["website"]
    if website:
        if "://" not in website:
            website = f"https://{website}"
        parsed = urlsplit(website)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise HTTPException(status_code=422, detail="Укажите корректный адрес сайта с http:// или https://")
        profile["website"] = website

    status = (
        "complete"
        if profile["company_name"] and (profile["description"] or profile["description_skipped"])
        else "incomplete"
    )
    saved = save_business_profile(user["id"], profile, status)
    if not saved:
        raise HTTPException(status_code=404, detail="Профиль компании не найден")
    return {"profile": _profile_view(saved)}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, str]:
    _check_csrf(request)
    token = request.cookies.get(_SESSION_COOKIE, "")
    if token:
        revoke_session(token)
    response.delete_cookie(_SESSION_COOKIE, path="/", secure=_secure_cookies(), httponly=True, samesite="lax")
    return {"status": "signed_out"}


def _canonical_login(value: str) -> str:
    login = value.strip().lower()
    if _LOGIN.fullmatch(login) is None:
        raise HTTPException(
            status_code=422,
            detail="Логин: 3–64 символа, латинские буквы, цифры, точка, дефис или подчёркивание",
        )
    return login


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, **_PASSWORD_SCRYPT)
    return "scrypt${n}${r}${p}${salt}${digest}".format(
        **_PASSWORD_SCRYPT,
        salt=salt.hex(),
        digest=digest.hex(),
    )


def _verify_password(password: str, encoded: str | None) -> bool:
    if encoded is None:
        salt = b"newbad-dummy-salt"
        hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, **_PASSWORD_SCRYPT)
        return False
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _check_csrf(request: Request) -> None:
    cookie = request.cookies.get(_CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="Обновите страницу и повторите запрос")


def _secure_cookies() -> bool:
    configured = os.environ.get("AUTH_COOKIE_SECURE")
    if configured is not None:
        return configured.strip().lower() == "true"
    return os.environ.get("APP_ENV", "development").strip().lower() == "production"


def _registration_enabled() -> bool:
    configured = os.environ.get("AUTH_REGISTRATION_ENABLED")
    if configured is not None:
        return configured.strip().lower() == "true"
    return os.environ.get("APP_ENV", "development").strip().lower() != "production"


def _auth_rate_keys(request: Request, login: str) -> list[str]:
    address = request.client.host if request.client else "unknown"
    return rate_limit_keys(address, login)


def _check_auth_rate(keys: list[str]) -> None:
    if rate_limited(keys):
        raise HTTPException(status_code=429, detail="Слишком много попыток. Попробуйте позже")


def _start_session(user_id: str, response: Response) -> None:
    token, max_age = create_session(user_id)
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=_secure_cookies(),
        samesite="lax",
        path="/",
    )


@app.post("/api/clients/{client_id}/events/{event_id}/card")
def event_card(client_id: str, event_id: str) -> Card:
    _check_id(client_id, "компании")
    _check_id(event_id, "события")
    card = build_card(client_id, event_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Компания или событие не найдены")
    return card


@app.get("/api/clients/{client_id}/events/{event_id}/steps")
def event_steps(client_id: str, event_id: str) -> dict:
    _check_id(client_id, "компании")
    _check_id(event_id, "события")
    if get_profile(client_id) is None:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    return {"steps": latest_steps(client_id, event_id), "trace": read_trace(client_id, event_id)}


@app.post("/api/clients/{client_id}/events/{event_id}/chat")
def event_chat(client_id: str, event_id: str, body: ChatQuestion) -> ChatReply:
    _check_id(client_id, "компании")
    _check_id(event_id, "события")
    card = build_card(client_id, event_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Компания или событие не найдены")
    try:
        return answer_from_card(card, body.question, client_id, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/clients/{client_id}/events/{event_id}/feedback")
def event_feedback(client_id: str, event_id: str, body: Feedback) -> dict[str, str]:
    _check_id(client_id, "компании")
    _check_id(event_id, "события")
    if get_profile(client_id) is None or find_news(event_id) is None:
        raise HTTPException(status_code=404, detail="Компания или событие не найдены")
    with connect() as connection:
        connection.execute(
            "INSERT INTO user_feedback (client_id, event_id, helpful, created_at) VALUES (?, ?, ?, ?)",
            (client_id, event_id, int(body.helpful), datetime.now(timezone.utc).isoformat()),
        )
    return {"status": "saved"}


@app.get("/api/metrics/feedback")
def feedback_metrics() -> dict[str, int | float]:
    """Обезличенная сводка для регулярного контроля качества прототипа."""
    with connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(helpful), 0) AS helpful FROM user_feedback"
        ).fetchone()
    total = int(row["total"])
    helpful = int(row["helpful"])
    return {"responses": total, "helpful": helpful, "not_helpful": total - helpful,
            "helpful_share": helpful / total if total else 0.0}


@app.post("/api/clients/{client_id}/news")
def paste_news(client_id: str, body: PasteNews) -> Card:
    _check_id(client_id, "компании")
    if get_profile(client_id) is None:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    try:
        item = remember_pasted(body.title, body.text, body.url, body.published_at, body.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    card = build_card(client_id, item.id)
    if card is None:
        raise HTTPException(status_code=404, detail="Не удалось собрать карточку")
    return card


@app.post("/api/settings")
def update_settings(body: SettingsUpdate) -> SettingsView:
    """Переключает режим текущего процесса. Файл .env и ключ не меняются."""
    if body.mode not in known_modes() or body.placement not in _PLACEMENTS:
        raise HTTPException(status_code=400, detail="Некорректные настройки")
    os.environ["LLM_MODE"] = body.mode
    os.environ["ALLOW_CLOUD"] = "true" if body.allow_cloud else "false"
    os.environ["LLM_PLACEMENT"] = body.placement
    saved = save_developer_settings({
        "task_routes": body.task_routes,
        "news_lookback_days": body.news_lookback_days,
        "news_retention_days": body.news_retention_days,
        "news_max_items": body.news_max_items,
        "news_collection_interval_hours": body.news_collection_interval_hours,
        "market_retention_days": body.market_retention_days,
        "market_max_items_per_instrument": body.market_max_items_per_instrument,
        "market_collection_interval_hours": body.market_collection_interval_hours,
        "market_categories": body.market_categories,
        "personal_analysis_trigger": body.personal_analysis_trigger,
        "personal_analysis_interval_hours": body.personal_analysis_interval_hours,
        "personal_analysis_max_candidates": body.personal_analysis_max_candidates,
        "insight_refresh_mode": body.insight_refresh_mode,
        "insight_refresh_interval_hours": body.insight_refresh_interval_hours,
        "insight_retention_days": body.insight_retention_days,
    })
    prune_insight_snapshots(saved["insight_retention_days"])
    prune_stored_news(
        retention_days=saved["news_retention_days"],
        max_items=saved["news_max_items"],
    )
    prune_market_data(
        retention_days=saved["market_retention_days"],
        max_items_per_instrument=saved["market_max_items_per_instrument"],
    )
    return settings()


@app.get("/api/settings")
def settings() -> SettingsView:
    news_settings = developer_settings()
    route = current_route()
    server = local_status()
    if route.provider == "local" and server != "up":
        active = "mock"
        model = "mock-model"
    else:
        active = route.mode
        model = route.model
    place, free = decide()
    choice = os.environ.get("LLM_PLACEMENT", "auto").strip().lower()
    if choice not in _PLACEMENTS:
        choice = "auto"
    return SettingsView(
        mode=os.environ.get("LLM_MODE", "mock"),
        allow_cloud=os.environ.get("ALLOW_CLOUD", "false").lower() == "true",
        active_generation=active,
        model=model,
        weights_from_browser=False,
        local_server=server,
        placement=placement_label(place, free),
        placement_choice=choice,
        **news_settings,
    )


@app.get("/api/settings/news/status")
def news_collection_status() -> dict:
    latest = latest_news_collection()
    return {
        "sources": [{"id": item.id, "name": item.name, "url": item.url} for item in load_news_sources()],
        "latest": latest,
        "stored_count": stored_news_count(),
        "settings": developer_settings(),
    }


@app.post("/api/settings/news/collect")
def collect_news_now() -> dict:
    return _collect_news()


@app.get("/api/settings/market/status")
def market_collection_status() -> dict:
    config = load_market_config()
    snapshot = market_snapshot()
    return {
        "provider": "MOEX ISS",
        "delayed": True,
        "categories": config["categories"],
        "latest": latest_market_collection(),
        **snapshot,
        "settings": developer_settings(),
    }


@app.post("/api/settings/market/collect")
def collect_market_now() -> dict:
    return _collect_market()


@app.get("/api/clients/{client_id}")
def client_profile(client_id: str) -> ClientProfile:
    _check_id(client_id, "компании")
    profile = get_profile(client_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    return profile


@app.get("/api/news")
def news():
    return load_news()


@app.get("/api/search")
def search(q: str = "") -> dict[str, list[SearchHit]]:
    if len(q) > _QUERY_LIMIT:
        raise HTTPException(status_code=400, detail="Слишком длинный запрос")
    return {"news": search_news(q), "bank": search_bank_analytics(q)}


class IndexStatus(BaseModel):
    name: str
    ready: bool
    count: int
    error: str = ""


class LlmStatus(BaseModel):
    mode: str
    provider: str
    model: str
    local_server: str
    allow_cloud: bool
    key_configured: bool


class ServiceStatus(BaseModel):
    status: str
    llm: LlmStatus
    indexes: list[IndexStatus]
    journal: list[str]


def _index_status(name: str, loader: Callable[[], list]) -> IndexStatus:
    try:
        count = len(loader())
    except CorpusError as exc:
        return IndexStatus(name=name, ready=False, count=0, error=str(exc))
    except Exception:
        return IndexStatus(name=name, ready=False, count=0, error="индекс недоступен")
    return IndexStatus(name=name, ready=True, count=count)


@app.get("/api/status")
def service_status() -> ServiceStatus:
    route = current_route()
    indexes = [_index_status("news", load_news), _index_status("bank", load_bank_documents)]
    journal = journal_tables()
    ready = all(item.ready for item in indexes) and bool(journal)
    return ServiceStatus(
        status="ok" if ready else "degraded",
        llm=LlmStatus(
            mode=route.mode,
            provider=route.provider,
            model=route.model,
            local_server=local_status(),
            allow_cloud=os.environ.get("ALLOW_CLOUD", "false").lower() == "true",
            key_configured=bool(os.environ.get("CASE_LLM_API_KEY", "").strip()),
        ),
        indexes=indexes,
        journal=journal,
    )


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "newbad",
        "journal": journal_tables(),
        "journal_empty": journal_is_empty(),
    }
