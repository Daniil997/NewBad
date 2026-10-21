import hashlib
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ValidationError

from app.corpus.search import NewsItem, load_bank_documents, load_news
from app.db import ROOT, connect, init_db
from app.profile.build import FIELD_LABELS, ClientProfile, Exposure, get_profile, load_profiles


class ScoreFactor(BaseModel):
    id: str
    label: str
    value: float
    note: str


class FeedItem(BaseModel):
    news_id: str
    title: str
    published_at: str
    event_type: str | None
    score: float
    band: str
    matched_features: list[str]
    factors: list[ScoreFactor]
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
    scoring = (ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8")
    parts.append("s:" + hashlib.sha256(scoring.encode("utf-8")).hexdigest()[:8])
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _score_feed(profile: ClientProfile, today: date, version: str) -> Feed:
    config = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
    payload = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))
    taxonomy = payload["event_types"]
    unknown = str((payload.get("classification") or {}).get("unknown") or "unknown")
    weights = config["weights"]
    news = load_news()
    newest = _newest_by_type(news, unknown)
    bank_topics = {item.topic for item in load_bank_documents()}
    scale = _scale(profile)
    gap, gap_note = _gap(profile, list(config["gap_fields"]))
    items = [
        _score_item(
            item, profile, taxonomy, unknown, weights, newest, bank_topics, scale, gap, gap_note, today, config
        )
        for item in news
    ]
    items.sort(key=_item_order, reverse=True)
    top, lower = _bands(items, config["thresholds"])
    return Feed(client_id=profile.client_id, corpus_version=version, top=top, less_relevant=lower)


def _score_item(
    item: NewsItem,
    profile: ClientProfile,
    taxonomy: dict,
    unknown: str,
    weights: dict,
    newest: dict[str, str],
    bank_topics: set[str],
    scale: float,
    gap: float,
    gap_note: str,
    today: date,
    config: dict,
) -> FeedItem:
    spec = taxonomy.get(item.event_type or "", {})
    matched = _matched(profile, spec.get("exposures") or [])
    confirmed = [exposure for exposure in matched if exposure.basis == "confirmed"]
    hypotheses = [exposure for exposure in matched if exposure.basis == "hypothesis"]
    event_title = str(spec.get("title") or item.event_title or item.event_type or "")
    factors: list[ScoreFactor] = []
    linked = False
    if confirmed:
        winner = max(confirmed, key=lambda exposure: (exposure.strength, exposure.id))
        factors.append(
            _factor(
                "exposure",
                "Подтверждённая экспозиция",
                weights["exposure"],
                winner.strength,
                f"подтверждено: {winner.title}",
            )
        )
        linked = True
    elif hypotheses:
        winner = max(hypotheses, key=lambda exposure: (exposure.strength, exposure.id))
        factors.append(
            _factor("industry", "Отрасль", weights["industry"], winner.strength, f"отрасль: {winner.title}")
        )
        linked = True
    elif item.event_type in {None, "", unknown}:
        factors.append(ScoreFactor(id="link", label="Связь с компанией", value=0, note="тип события неизвестен"))
    else:
        factors.append(ScoreFactor(id="link", label="Связь с компанией", value=0, note="нет связи с профилем компании"))
    if linked and event_title:
        factors[-1].note = f"{factors[-1].note}, тип «{event_title}»"
    bank = 1.0 if bank_topics & set(spec.get("bank_topics") or []) else 0.0
    factors.append(_factor("bank", "Аналитика банка", weights["bank"], bank, "есть аналитика банка по этой теме"))
    quality, quality_note = _source_quality(item.url, config["source_quality"])
    factors.append(_factor("source", "Качество источника", weights["source"], quality, quality_note))
    fresh = _freshness(item.published_at, today, float(config["freshness"]["half_life_days"]))
    factors.append(_factor("fresh", "Свежесть", weights["fresh"], fresh, "учтена дата публикации"))
    if linked:
        factors.append(_factor("scale", "Масштаб", weights["scale"], scale, "масштаб компании учтён"))
    entity = _entity_match(profile, f"{item.title} {item.excerpt}", {exposure.id for exposure in matched})
    entity_note = "в тексте есть страна, город, регион или контрагент"
    factors.append(_factor("entity", "Совпадение в тексте", weights["entity"], entity, entity_note))
    same_type = item.event_type not in {None, "", unknown}
    duplicate = 1.0 if same_type and item.published_at < newest.get(item.event_type, item.published_at) else 0.0
    factors.append(
        _factor("duplicate", "Повтор типа", -weights["duplicate"], duplicate, "есть более новая новость этого типа")
    )
    factors.append(_factor("gap", "Пропуски в профиле", -weights["gap"], gap, gap_note))
    kept = [factor for factor in factors if factor.value != 0 or factor.id == "link"]
    score = round(sum(factor.value for factor in factors), 4)
    return FeedItem(
        news_id=item.id,
        title=item.title,
        published_at=item.published_at,
        event_type=item.event_type,
        score=score,
        band="low",
        matched_features=[exposure.id for exposure in matched],
        factors=kept,
        reason=_reason(kept),
    )


def _bands(items: list[FeedItem], thresholds: dict) -> tuple[list[FeedItem], list[FeedItem]]:
    high_bar = float(thresholds["high"])
    extra_bar = float(thresholds["extra"])
    top_n = int(thresholds["top_n"])
    high: list[FeedItem] = []
    extra: list[FeedItem] = []
    low: list[FeedItem] = []
    for item in items:
        confirmed = any(factor.id == "exposure" for factor in item.factors)
        industry = any(factor.id == "industry" for factor in item.factors)
        if confirmed and item.score >= high_bar and len(high) < top_n:
            high.append(item.model_copy(update={"band": "high"}))
        elif (confirmed or industry) and item.score >= extra_bar:
            extra.append(item.model_copy(update={"band": "extra"}))
        else:
            low.append(item.model_copy(update={"band": "low"}))
    return high, extra + low


def _factor(factor_id: str, label: str, weight: float, raw: float, note: str) -> ScoreFactor:
    return ScoreFactor(id=factor_id, label=label, value=round(weight * raw, 4), note=note)


def _matched(profile: ClientProfile, event_exposures: list[str]) -> list[Exposure]:
    owned = {item.id: item for item in profile.exposures}
    return [owned[item] for item in event_exposures if item in owned]


def _entity_match(profile: ClientProfile, text: str, matched_ids: set[str]) -> float:
    """Страна и контрагент не добавляют балл повторно, если эта экспозиция уже учтена."""
    haystack = text.lower()
    named = [item.client_id.lower() for item in profile.counterparties]
    if "counterparty" not in matched_ids and any(name and name in haystack for name in named):
        return 1.0
    for feature in profile.features:
        if feature.id == "FTS_COUNTRY_NAME" and "country_exposure" in matched_ids:
            continue
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


def _newest_by_type(news, unknown: str) -> dict[str, str]:
    newest: dict[str, str] = {}
    for item in news:
        if not item.event_type or item.event_type == unknown:
            continue
        current = newest.get(item.event_type)
        if current is None or item.published_at > current:
            newest[item.event_type] = item.published_at
    return newest


def _gap(profile: ClientProfile, fields: list[str]) -> tuple[float, str]:
    missing = [field for field in fields if field in profile.missing_data]
    if not fields or not missing:
        return 0.0, ""
    labels = [FIELD_LABELS.get(field, field) for field in missing]
    return len(missing) / len(fields), "штраф за пропуски: " + ", ".join(labels)


def _source_quality(url: str, table: dict) -> tuple[float, str]:
    values = table["values"]
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return float(values["none"]), "у новости нет адреса источника"
    for level, note in (("high", "качество источника: высокое"), ("medium", "качество источника: среднее")):
        if any(host == name or host.endswith("." + name) for name in table[level]):
            return float(values[level]), note
    return float(values["low"]), "качество источника: низкое"


def _item_order(item: FeedItem) -> tuple[float, str, str]:
    return (item.score, item.published_at, item.news_id)


def _reason(factors: list[ScoreFactor]) -> str:
    notes = [factor.note for factor in factors if factor.note]
    return "; ".join(notes) if notes else "нет совпадения с профилем"


def _read_cache(client_id: str, version: str) -> Feed | None:
    init_db()
    with connect() as connection:
        row = connection.execute(
            "SELECT payload FROM feed_cache WHERE client_id = ? AND corpus_version = ?",
            (client_id, version),
        ).fetchone()
    if row is None:
        return None
    try:
        return Feed.model_validate_json(row["payload"])
    except ValidationError:
        return None


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
