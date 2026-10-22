import os
import re
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agent.chat import ChatReply, answer_from_card
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
from app.db import init_db, journal_is_empty, journal_tables
from app.profile.build import ClientProfile, ClientSummary, get_profile, load_profiles
from app.schemas.card import Card
from app.scoring.feed import Feed, build_feed
from app.llm.placement import decide, placement_label
from app.llm.router import current_route, local_status
from app.tracing.journal import latest_steps, read_trace

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
_QUERY_LIMIT = 300


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


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


class SettingsView(BaseModel):
    mode: str
    allow_cloud: bool
    active_generation: str
    model: str
    weights_from_browser: bool
    local_server: str
    placement: str


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
        return answer_from_card(card, body.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.get("/api/settings")
def settings() -> SettingsView:
    route = current_route()
    server = local_status()
    if route.provider == "local" and server != "up":
        active = "mock"
        model = "mock-model"
    else:
        active = route.mode
        model = route.model
    place, free = decide()
    return SettingsView(
        mode=os.environ.get("LLM_MODE", "mock"),
        allow_cloud=os.environ.get("ALLOW_CLOUD", "false").lower() == "true",
        active_generation=active,
        model=model,
        weights_from_browser=False,
        local_server=server,
        placement=placement_label(place, free),
    )


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
