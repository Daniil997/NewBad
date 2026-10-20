from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.corpus.search import SearchHit, load_news, search_bank_analytics, search_news
from app.db import init_db, journal_is_empty, journal_tables
from app.profile.build import ClientProfile, ClientSummary, get_profile, load_profiles


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
