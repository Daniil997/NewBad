import hashlib
from datetime import date, datetime, timezone

import yaml
from pydantic import BaseModel

from app.corpus.search import load_bank_documents, load_news
from app.db import ROOT, connect, init_db
from app.profile.build import ClientProfile, get_profile, load_profiles


class FeedItem(BaseModel):
    news_id: str
    title: str
    published_at: str
    event_type: str | None
    score: float
    matched_features: list[str]
    reason: str


class Feed(BaseModel):
    client_id: str
    corpus_version: str
    top: list[FeedItem]
    less_relevant: list[FeedItem]


def build_feed(client_id: str, today: date | None = None) -> Feed | None:
    profile = get_profile(client_id)
    if profile is None:
        return None
    version = corpus_version()
    cached = _read_cache(client_id, version)
    if cached is not None:
        return cached
    feed = _score_feed(profile, today or date.today(), version)
    _write_cache(feed)
    return feed


def corpus_version() -> str:
    parts = [f"n:{item.id}:{item.published_at}" for item in load_news()]
    parts.extend(f"b:{item.doc_id}:{item.published_at}" for item in load_bank_documents())
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _score_feed(profile: ClientProfile, today: date, version: str) -> Feed:
    config = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
    taxonomy = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))["event_types"]
    weights = config["weights"]
    news = load_news()
    newest = _newest_by_type(news)
    bank_topics = {item.topic for item in load_bank_documents()}
    scale = _scale(profile)
    items: list[FeedItem] = []
    for item in news:
        spec = taxonomy.get(item.event_type or "", {})
        matched = _matched(profile, spec.get("exposures") or [])
        strength = max((exposure.strength for exposure in profile.exposures if exposure.id in matched), default=0.0)
        entity = _entity_match(profile, f"{item.title} {item.excerpt}")
        bank = 1.0 if bank_topics & set(spec.get("bank_topics") or []) else 0.0
        fresh = _freshness(item.published_at, today, float(config["freshness"]["half_life_days"]))
        duplicate = 1.0 if item.event_type and item.published_at < newest.get(item.event_type, item.published_at) else 0.0
        score = (
            weights["type"] * strength
            + weights["entity"] * entity
            + weights["scale"] * scale
            + weights["bank"] * bank
            + weights["fresh"] * fresh
            - weights["dup"] * duplicate
        )
        items.append(
            FeedItem(
                news_id=item.id,
                title=item.title,
                published_at=item.published_at,
                event_type=item.event_type,
                score=round(score, 4),
                matched_features=matched,
                reason=_reason(matched),
            )
        )
    items.sort(key=lambda row: (row.score, row.published_at), reverse=True)
    show = float(config["thresholds"]["show"])
    top_n = int(config["thresholds"]["top_n"])
    visible = [row for row in items if row.score >= show]
    return Feed(
        client_id=profile.client_id,
        corpus_version=version,
        top=visible[:top_n],
        less_relevant=visible[top_n:] + [row for row in items if row.score < show],
    )


def _matched(profile: ClientProfile, event_exposures: list[str]) -> list[str]:
    owned = {item.id for item in profile.exposures}
    return [item for item in event_exposures if item in owned]


def _entity_match(profile: ClientProfile, text: str) -> float:
    haystack = text.lower()
    if any(item.client_id.lower() in haystack for item in profile.counterparties):
        return 1.0
    for feature in profile.features:
        if feature.id in {"FTS_COUNTRY_NAME", "CITY", "REGION"} and feature.value.lower() in haystack:
            return 0.8
    return 0.0


def _scale(profile: ClientProfile) -> float:
    revenues = []
    own = None
    for item in load_profiles():
        for feature in item.features:
            if feature.id == "REVENUE_COMPANY":
                value = float(feature.value)
                revenues.append(value)
                if item.client_id == profile.client_id:
                    own = value
    if own is None or not revenues:
        return 0.0
    return own / max(revenues)


def _freshness(published_at: str, today: date, half_life_days: float) -> float:
    published = date.fromisoformat(published_at)
    age = max((today - published).days, 0)
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (age / half_life_days)


def _newest_by_type(news) -> dict[str, str]:
    newest: dict[str, str] = {}
    for item in news:
        if not item.event_type:
            continue
        current = newest.get(item.event_type)
        if current is None or item.published_at > current:
            newest[item.event_type] = item.published_at
    return newest


def _reason(matched: list[str]) -> str:
    if not matched:
        return "нет совпадения с профилем"
    return "совпали: " + ", ".join(matched)


def _read_cache(client_id: str, version: str) -> Feed | None:
    init_db()
    with connect() as connection:
        row = connection.execute(
            "SELECT payload FROM feed_cache WHERE client_id = ? AND corpus_version = ?",
            (client_id, version),
        ).fetchone()
    if row is None:
        return None
    return Feed.model_validate_json(row["payload"])


def _write_cache(feed: Feed) -> None:
    init_db()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO feed_cache (client_id, corpus_version, payload, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id, corpus_version) DO UPDATE SET payload = excluded.payload
            """,
            (
                feed.client_id,
                feed.corpus_version,
                feed.model_dump_json(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
