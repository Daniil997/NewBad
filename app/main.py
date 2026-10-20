from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db, journal_is_empty, journal_tables


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Что важно для вашей компании", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "newbad",
        "journal": journal_tables(),
        "journal_empty": journal_is_empty(),
    }
