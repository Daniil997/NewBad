import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.agent.chat import ChatReply, answer_from_card
from app.agent.loop import build_card
from app.agent.pasted import remember_pasted
from app.corpus.search import SearchHit, load_news, search_bank_analytics, search_news
from app.db import init_db, journal_is_empty, journal_tables
from app.profile.build import ClientProfile, ClientSummary, get_profile, load_profiles
from app.schemas.card import Card
from app.scoring.feed import Feed, build_feed
from app.llm.placement import decide, placement_label
from app.llm.router import current_route, local_status
from app.tracing.journal import latest_steps


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Что важно для вашей компании", lifespan=lifespan)


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
    feed = build_feed(client_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    return feed


class PasteNews(BaseModel):
    title: str
    text: str
    url: str = ""
    published_at: str = ""
    source: str = ""


class ChatQuestion(BaseModel):
    question: str


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
    card = build_card(client_id, event_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Компания или событие не найдены")
    return card


@app.get("/api/clients/{client_id}/events/{event_id}/steps")
def event_steps(client_id: str, event_id: str) -> dict[str, list[str]]:
    if get_profile(client_id) is None:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    return {"steps": latest_steps(client_id, event_id)}


@app.post("/api/clients/{client_id}/events/{event_id}/chat")
def event_chat(client_id: str, event_id: str, body: ChatQuestion) -> ChatReply:
    card = build_card(client_id, event_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Компания или событие не найдены")
    try:
        return answer_from_card(card, body.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/clients/{client_id}/news")
def paste_news(client_id: str, body: PasteNews) -> Card:
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
    active = route.mode if server == "up" else "mock"
    place, free = decide()
    return SettingsView(
        mode=os.environ.get("LLM_MODE", "mock"),
        allow_cloud=os.environ.get("ALLOW_CLOUD", "false").lower() == "true",
        active_generation=active,
        model=route.model if route.provider == "local" else "mock-model",
        weights_from_browser=False,
        local_server=server,
        placement=placement_label(place, free),
    )


@app.get("/api/clients/{client_id}")
def client_profile(client_id: str) -> ClientProfile:
    profile = get_profile(client_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    return profile


@app.get("/api/news")
def news():
    return load_news()


@app.get("/api/search")
def search(q: str = "") -> dict[str, list[SearchHit]]:
    return {"news": search_news(q), "bank": search_bank_analytics(q)}


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "newbad",
        "journal": journal_tables(),
        "journal_empty": journal_is_empty(),
    }
