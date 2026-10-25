"""Shared bank knowledge and profile-specific applications, stored entirely in SQLite."""

import json
import os
import re
import sqlite3
import textwrap
import uuid
from datetime import date
from pathlib import Path

from app.user_store import ROOT, connect_app, now_utc

TABLES = ("bank_documents", "bank_pages", "bank_passages", "bank_corpus_meta")
BANK_MATCH_VERSION = "bank-match-v3"
BANK_TOPICS = {"key_rate", "inflation", "macro", "strategy", "bonds", "consumption", "gold", "metals"}


def create_corpus_schema(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS bank_documents (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, published_at TEXT NOT NULL,
            period TEXT NOT NULL, topic TEXT NOT NULL, type TEXT NOT NULL,
            source_name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, page_count INTEGER NOT NULL,
            notes TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS bank_pages (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES bank_documents(id),
            page INTEGER NOT NULL, raw_text TEXT NOT NULL, text TEXT NOT NULL,
            extraction_kind TEXT NOT NULL, quality TEXT NOT NULL,
            UNIQUE(document_id, page))""",
        """CREATE TABLE IF NOT EXISTS bank_passages (
            id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES bank_pages(id),
            text TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(page_id, text))""",
        "CREATE INDEX IF NOT EXISTS bank_passages_page_idx ON bank_passages(page_id)",
        "CREATE TABLE IF NOT EXISTS bank_corpus_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    )
    for statement in statements:
        connection.execute(statement)


def seed_path() -> Path | None:
    configured = os.environ.get("BANK_CORPUS_PATH")
    if configured == "":
        return None
    path = Path(configured) if configured else ROOT / "data" / "bank" / "corpus.sqlite3"
    return path if path.is_absolute() else ROOT / path


def import_corpus() -> bool:
    """Install a versioned, account-free SQLite bundle. Never replace editorial assessments."""
    path = seed_path()
    if path is None:
        return False
    if not path.is_file():
        raise RuntimeError(f"Не найдена база банковской аналитики: {path.name}")
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        version = source.execute("SELECT value FROM bank_corpus_meta WHERE key='version'").fetchone()[0]
        with connect_app() as target:
            target.execute("BEGIN IMMEDIATE")
            current = target.execute("SELECT value FROM bank_corpus_meta WHERE key='version'").fetchone()
            if current and current[0] == version:
                return False
            protected = {row[0] for row in target.execute(
                "SELECT id FROM bank_documents WHERE edited=1 OR deleted=1"
            )}
            page_documents = dict(source.execute("SELECT id,document_id FROM bank_pages"))
            # Upsert stable IDs so historical source links remain valid across imports.
            for table in TABLES:
                columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
                assignments = ", ".join(f"{name}=excluded.{name}" for name in columns[1:])
                key = columns[0]
                placeholders = ",".join("?" for _ in columns)
                rows = source.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
                if table == "bank_documents":
                    rows = [row for row in rows if row[0] not in protected]
                elif table == "bank_pages":
                    rows = [row for row in rows if row[1] not in protected]
                elif table == "bank_passages":
                    rows = [row for row in rows if page_documents[row[1]] not in protected]
                else:
                    rows = [row for row in rows if row[0] != "manual_revision"]
                target.executemany(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
                    f"ON CONFLICT({key}) DO UPDATE SET {assignments}",
                    rows,
                )
        return True
    finally:
        source.close()


def corpus_version() -> str:
    with connect_app() as connection:
        row = connection.execute("SELECT value FROM bank_corpus_meta WHERE key='version'").fetchone()
        revision = connection.execute("SELECT value FROM bank_corpus_meta WHERE key='manual_revision'").fetchone()
    version = row[0] if row else "empty"
    # Keep the seed version separate: restarting/importing must preserve user materials.
    return f"{version}:manual-{revision[0]}" if revision else version


class MaterialConflict(ValueError):
    pass


def _bump_corpus_revision(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO bank_corpus_meta (key,value) VALUES ('manual_revision','1') "
        "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1",
    )


def _material_chunks(text: str) -> list[str]:
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?…])\s+|\n+", text):
        for part in textwrap.wrap(sentence, width=1400, break_on_hyphens=False):
            if current and len(current) + len(part) + 1 > 1600:
                chunks.append(current)
                current = ""
            current = f"{current} {part}".strip()
    if current:
        chunks.append(current)
    return list(dict.fromkeys(chunks))


def _write_material_passages(connection, page_id: str, text: str, *, reviewed: bool) -> None:
    revision_id = uuid.uuid4().hex
    connection.executemany(
        "INSERT INTO bank_passages (id,page_id,text,reviewed,active,position) VALUES (?,?,?,?,1,?) "
        "ON CONFLICT(page_id,text) DO UPDATE SET reviewed=excluded.reviewed,active=1,position=excluded.position",
        [(f"{page_id}:{revision_id}:{index:05}", page_id, chunk, int(reviewed), index)
         for index, chunk in enumerate(_material_chunks(text))],
    )


def add_text_material(*, title: str, published_at: str, text: str, period: str = "",
                      notes: str = "", topic: str = "auto", document_id: str | None = None,
                      reviewed_text: str | None = None, expected_revision: int | None = None) -> str:
    """Store editorial text in the shared corpus and invalidate all matching caches atomically."""
    import hashlib

    from app.news_topics import news_topic

    if topic == "auto":
        event_type, _ = news_topic(title, text)
        topic = {"monetary_policy": "key_rate", "inflation_macro": "inflation",
                 "consumption_labor": "consumption", "precious_metals": "gold",
                 "fx": "strategy", "money_market": "key_rate"}.get(event_type, event_type)
        if topic not in BANK_TOPICS:
            topic = "unknown"
    elif topic not in BANK_TOPICS:
        raise ValueError("Неизвестная тема материала")
    digest = hashlib.sha256(json.dumps(
        [title, published_at, period, topic, " ".join(text.split())], ensure_ascii=False,
    ).encode()).hexdigest()
    with connect_app() as connection:
        connection.execute("BEGIN IMMEDIATE")
        old = None
        if document_id is not None:
            old = connection.execute("SELECT * FROM bank_documents WHERE id=? AND deleted=0", (document_id,)).fetchone()
            if old is None:
                raise LookupError("Материал банка не найден")
            if expected_revision is not None and old["revision"] != expected_revision:
                raise MaterialConflict("Материал уже изменён. Откройте его заново перед сохранением.")
        duplicate = connection.execute(
            "SELECT id FROM bank_documents WHERE (content_hash=? OR sha256=?) AND deleted=0 AND id<>?",
            (digest, digest, document_id or ""),
        ).fetchone()
        if duplicate:
            raise MaterialConflict("Такой материал уже есть в библиотеке")
        if old is None:
            document_id = f"manual-{uuid.uuid4().hex}"
            page_id = f"{document_id}:1"
            source_hash = hashlib.sha256(f"{document_id}:{digest}".encode()).hexdigest()
            connection.execute(
                "INSERT INTO bank_documents "
                "(id,title,published_at,period,topic,type,source_name,sha256,page_count,notes,content_hash) "
                "VALUES (?,?,?,?,?,'manual','Ручной ввод',?,1,?,?)",
                (document_id, title, published_at, period, topic, source_hash, notes, digest),
            )
            connection.execute(
                "INSERT INTO bank_pages (id,document_id,page,raw_text,text,extraction_kind,quality) "
                "VALUES (?,?,1,?,?,'manual','editorial')", (page_id, document_id, text, text),
            )
            _write_material_passages(connection, page_id, text, reviewed=True)
        else:
            source_hash = hashlib.sha256(f"{document_id}:{digest}".encode()).hexdigest()
            pages = connection.execute("SELECT id,text FROM bank_pages WHERE document_id=? ORDER BY page", (document_id,)).fetchall()
            page_id = pages[0]["id"]
            text_changed = text != "\n\n".join(page["text"] for page in pages if page["text"])
            old_reviewed = "\n\n".join(row[0] for row in connection.execute(
                "SELECT c.text FROM bank_passages c JOIN bank_pages p ON p.id=c.page_id "
                "WHERE p.document_id=? AND c.reviewed=1 AND c.active=1 ORDER BY p.page,c.position,c.id", (document_id,),
            ))
            # Keep old fragments for historical report FKs. Active retrieval sees only current text.
            if text_changed:
                connection.execute("UPDATE bank_passages SET active=0 WHERE page_id IN (SELECT id FROM bank_pages WHERE document_id=?)", (document_id,))
                connection.execute("UPDATE bank_pages SET text='' WHERE document_id=?", (document_id,))
                connection.execute("UPDATE bank_pages SET text=?,quality='editorial' WHERE id=?", (text, page_id))
                _write_material_passages(connection, page_id, text, reviewed=old["type"] == "manual")
            if old["type"] != "manual" and reviewed_text is not None and (text_changed or reviewed_text != old_reviewed):
                connection.execute("UPDATE bank_passages SET active=0 WHERE reviewed=1 AND page_id IN (SELECT id FROM bank_pages WHERE document_id=?)", (document_id,))
                _write_material_passages(connection, page_id, reviewed_text, reviewed=True)
            connection.execute(
                "UPDATE bank_documents SET title=?,published_at=?,period=?,topic=?,sha256=?,notes=?,"
                "edited=1,revision=revision+1,page_count=?,content_hash=? WHERE id=?",
                (title, published_at, period, topic, source_hash, notes, 1 if text_changed else old["page_count"], digest, document_id),
            )
        _bump_corpus_revision(connection)
    return document_id


def delete_material(document_id: str, *, expected_revision: int | None = None) -> None:
    with connect_app() as connection:
        connection.execute("BEGIN IMMEDIATE")
        old = connection.execute("SELECT revision FROM bank_documents WHERE id=? AND deleted=0", (document_id,)).fetchone()
        if old is None:
            raise LookupError("Материал банка не найден")
        if expected_revision is not None and old["revision"] != expected_revision:
            raise MaterialConflict("Материал уже изменён. Откройте его заново перед удалением.")
        connection.execute("UPDATE bank_documents SET deleted=1,revision=revision+1 WHERE id=?", (document_id,))
        _bump_corpus_revision(connection)


def corpus_status() -> dict:
    with connect_app() as connection:
        return {"version": corpus_version(), **{
            "documents": connection.execute("SELECT COUNT(*) FROM bank_documents WHERE deleted=0").fetchone()[0],
            "pages": connection.execute("SELECT COUNT(*) FROM bank_pages p JOIN bank_documents d ON d.id=p.document_id WHERE d.deleted=0 AND p.text<>''").fetchone()[0],
            "passages": connection.execute("SELECT COUNT(*) FROM bank_passages c JOIN bank_pages p ON p.id=c.page_id JOIN bank_documents d ON d.id=p.document_id WHERE d.deleted=0 AND c.active=1").fetchone()[0],
        }, "reviewed_passages": connection.execute(
            "SELECT COUNT(*) FROM bank_passages c JOIN bank_pages p ON p.id=c.page_id "
            "JOIN bank_documents d ON d.id=p.document_id WHERE c.reviewed=1 AND c.active=1 AND d.deleted=0"
        ).fetchone()[0]}


def passages(*, reviewed_only: bool = False) -> list[dict]:
    with connect_app() as connection:
        rows = connection.execute(
            "SELECT c.id, c.text, c.reviewed, p.page, p.document_id, d.title, d.published_at, "
            "d.period, d.topic, d.type, d.sha256 FROM bank_passages c "
            "JOIN bank_pages p ON p.id=c.page_id JOIN bank_documents d ON d.id=p.document_id "
            "WHERE d.deleted=0 AND c.active=1 " + ("AND c.reviewed=1 " if reviewed_only else "") + "ORDER BY c.id"
        ).fetchall()
    return [dict(row) for row in rows]


def search_passages(query: str, *, limit: int = 5, as_of: date | None = None,
                    reviewed_only: bool = False, topics: set[str] | None = None) -> list[dict]:
    from app.corpus.search import _COMMON, _STOP, _rank_tokens, _tokenize

    terms = [word for word in _tokenize(query, stem=True) if word not in _STOP and word not in _COMMON]
    if not terms:
        return []
    anchor = as_of or date.today()
    candidates = []
    for row in passages(reviewed_only=reviewed_only):
        published = row["published_at"]
        if published == "unknown":
            continue
        # Month precision must not leak a publication into earlier days of that month.
        if len(published) == 7:
            import calendar

            year, month = map(int, published.split("-"))
            published_on = date(year, month, calendar.monthrange(year, month)[1])
        else:
            published_on = date.fromisoformat(published)
        if published_on > anchor or (topics is not None and row["topic"] not in topics):
            continue
        row["stale"] = (anchor - published_on).days > 180
        candidates.append(row)
    if not candidates:
        return []
    texts = [f"{row['title']} {row['text']}" for row in candidates]
    ranked = _rank_tokens(terms, texts)
    result = []
    for index, score in ranked:
        row = candidates[index]
        matches = set(terms) & set(_tokenize(row["text"], stem=True))
        if not matches:
            continue
        result.append({**row, "score": round(score, 4), "matched_terms": len(matches)})
    result.sort(key=lambda row: (not row["stale"], row["matched_terms"], row["score"], row["published_at"]), reverse=True)
    return result[:limit]


def bank_evidence(profile: dict, news=None, question: str = "", *, limit: int = 2,
                  allow_weak_profile: bool = False) -> tuple[list[dict], str]:
    """Retrieve by event first; company signals rank the sources, never manufacture bank claims."""
    from app.agent.business_context import company_context
    from app.personal_feed import evaluate
    from app.relevance import concepts, exclusion_reason

    reason = "Подбор по вопросу и сведениям о компании"
    anchor = date.today()
    if news is not None:
        excluded = exclusion_reason(profile, news)
        if excluded:
            return [], excluded
        relevance = evaluate(profile, news)
        if not relevance["relevant"] and not allow_weak_profile:
            return [], "Связь события с профилем не подтверждена"
        reason = "; ".join(relevance["reasons"])
        anchor = date.fromisoformat(news.published_at)
    event_query = f"{news.title} {news.excerpt[:2500]}" if news else question
    if question:
        event_query += " " + question
    # Headline concepts define the event; long official releases must not swamp the headline.
    focus = concepts(news.title if news else question)
    all_concepts = concepts(event_query)
    if not focus:
        event_concepts = {"monetary_policy": "rates", "inflation_macro": "inflation", "fx": "fx",
                          "bonds": "bonds", "money_market": "rates", "macro": "macro"}
        inferred = event_concepts.get(news.event_type) if news else None
        if inferred is None:
            return [], "В заголовке нет подтвержденной темы, соответствующей банковскому материалу"
        focus = {inferred}
    topics = None
    if news is not None and not question:
        from app.news_topics import load_taxonomy

        event_config = load_taxonomy().get("event_types", {}).get(news.event_type or "unknown")
        if event_config is not None and news.event_type != "unknown":
            topics = set(event_config.get("bank_topics", []))
    expansions = {"fuel": "топливо бензин дизель", "rates": "ключевая ставка кредитование",
                  "demand": "потребление спрос розничная торговля", "labor": "рынок труда зарплаты",
                  "fx": "валюта доллар рубль", "trade": "экспорт импорт", "gold": "золото",
                  "bonds": "облигации купон", "inflation": "инфляция цены"}
    expanded = event_query + " " + " ".join(expansions.get(name, "") for name in sorted(focus))
    event_hits = search_passages(expanded, limit=50, as_of=anchor, reviewed_only=True, topics=topics)
    eligible = []
    for hit in event_hits:
        source_concepts = concepts(hit["text"])
        topic_concepts = {"key_rate": "rates", "inflation": "inflation", "consumption": "demand", "gold": "gold", "bonds": "bonds"}
        if hit["topic"] in topic_concepts:
            source_concepts.add(topic_concepts[hit["topic"]])
        if hit["topic"] == "gold" and "gold" not in all_concepts:
            continue
        if focus == {"demand"} and hit["topic"] not in {"consumption", "macro"}:
            continue
        if focus == {"labor"} and hit["topic"] not in {"consumption", "macro"}:
            continue
        assets = focus & {"fuel", "oil", "gas", "coal", "metals", "gold"}
        if assets and not assets & source_concepts:
            continue
        shared = focus & source_concepts
        if not shared:
            continue
        # A September forecast of the October meeting must not masquerade as its outcome.
        forecast = re.search(r"ожидаем.*?заседании (\d{1,2}) (\w+)", hit["text"], re.IGNORECASE)
        if forecast:
            months = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
                      "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
            month = months.get(forecast[2].casefold())
            if month and anchor >= date(int(hit["published_at"][:4]), month, int(forecast[1])):
                continue
        hit["concept_score"] = len(shared) * 3 + len(all_concepts & concepts(hit["text"]))
        preferred = {"rates": "key_rate", "inflation": "inflation", "fuel": "inflation",
                     "demand": "consumption", "gold": "gold", "bonds": "bonds"}
        if hit["topic"] in {preferred.get(name) for name in focus}:
            hit["concept_score"] += 4
        if "fuel" in focus and re.search(r"рынке топлива|проинфляционным риском", hit["text"], re.IGNORECASE):
            hit["concept_score"] += 2
        if re.search(r"мы ожидаем|наш прогноз|мы понижаем", hit["text"], re.IGNORECASE):
            hit["concept_score"] -= 1
        eligible.append(hit)
    event_hits = eligible
    context = json.dumps(company_context(profile), ensure_ascii=False)
    profile_terms = set(re.findall(r"[а-яёa-z]{5,}", context.lower()))
    event_hits.sort(key=lambda row: (
        not row["stale"], row["concept_score"],
        len(profile_terms & set(re.findall(r"[а-яёa-z]{5,}", row["text"].lower()))),
        row["published_at"], row["matched_terms"], row["score"],
    ), reverse=True)
    chosen, documents = [], set()
    for hit in event_hits:
        if hit["stale"] or hit["document_id"] in documents:
            continue
        documents.add(hit["document_id"])
        chosen.append({
            "evidence_id": f"bank:{hit['id']}", "passage_id": hit["id"],
            "document_id": hit["document_id"], "title": hit["title"], "text": hit["text"],
            "published_at": hit["published_at"], "period": hit["period"], "page": hit["page"],
            "source_hash": hit["sha256"], "kind": "bank", "source": "Альфа-Банк", "url": "",
        })
        if len(chosen) == limit:
            break
    return chosen, reason


def matching_version() -> str:
    from app.personal_feed import scoring_version

    return f"{corpus_version()}:{BANK_MATCH_VERSION}:{scoring_version()}"


def ensure_recommendations(profile_id: str, news_items: list | None = None,
                           model_results: dict | None = None, expected_profile_version: int | None = None) -> None:
    from app.corpus.search import load_news
    from app.personal_feed import article_hash

    with connect_app() as connection:
        profile_row = connection.execute("SELECT * FROM business_profiles WHERE id=?", (profile_id,)).fetchone()
        existing = {row["news_id"]: dict(row) for row in connection.execute(
            "SELECT * FROM profile_bank_recommendations WHERE profile_id=?", (profile_id,)
        )}
    if profile_row is None:
        return
    if expected_profile_version is not None and profile_row["profile_version"] != expected_profile_version:
        return
    profile = json.loads(profile_row["profile_json"])
    version = matching_version()
    for news in (news_items if news_items is not None else load_news()):
        old = existing.get(news.id)
        fingerprint = article_hash(news)
        model = (model_results or {}).get(news.id)
        if old and (old["origin"] == "editorial" or (model is None and
            old["profile_version"] == profile_row["profile_version"] and old["news_hash"] == fingerprint
            and old["corpus_version"] == version
        )):
            continue
        evidence, reason = bank_evidence(profile, news)
        if model is not None and "bank_source_ids" in model:
            from app.personal_feed import calibrate_model_result

            candidates, reason = bank_evidence(profile, news, limit=5, allow_weak_profile=True)
            ids = model["bank_source_ids"]
            if (isinstance(ids, list) and len(ids) <= 2 and all(isinstance(value, str) for value in ids)
                    and set(ids) <= {item["passage_id"] for item in candidates}):
                evidence = [item for item in candidates if item["passage_id"] in ids]
                if not calibrate_model_result(profile, news, model)["relevant"]:
                    evidence = []
                reason += "; Проверка модели: " + str(model.get("bank_selection_reason") or "Оценена связь источника с событием")[:400]
        # Store a negative match too; it must be invalidated when profile/news/corpus changes.
        with connect_app() as connection:
            current_profile = connection.execute(
                "SELECT profile_version FROM business_profiles WHERE id=?", (profile_id,)
            ).fetchone()
            if current_profile is None or current_profile[0] != profile_row["profile_version"]:
                return
            if connection.execute("SELECT id FROM news_articles WHERE id=?", (news.id,)).fetchone() is None:
                continue
            connection.execute(
                "INSERT INTO profile_bank_recommendations "
                "(profile_id,news_id,text,page,updated_at,origin,sources_json,profile_version,news_hash,corpus_version,match_reason) "
                "VALUES (?,?,?,NULL,?,'corpus',?,?,?,?,?) ON CONFLICT(profile_id,news_id) DO UPDATE SET "
                "text=excluded.text,page=NULL,updated_at=excluded.updated_at,sources_json=excluded.sources_json,"
                "profile_version=excluded.profile_version,news_hash=excluded.news_hash,"
                "corpus_version=excluded.corpus_version,match_reason=excluded.match_reason "
                "WHERE profile_bank_recommendations.origin='corpus'",
                (profile_id, news.id, "\n\n".join(item["text"] for item in evidence), now_utc(),
                 json.dumps(evidence, ensure_ascii=False), profile_row["profile_version"], fingerprint, version, reason),
            )
            row = connection.execute(
                "SELECT origin FROM profile_bank_recommendations WHERE profile_id=? AND news_id=?", (profile_id, news.id)
            ).fetchone()
            if row is None or row[0] != "corpus":
                continue
            connection.execute("DELETE FROM profile_bank_sources WHERE profile_id=? AND news_id=?", (profile_id, news.id))
            connection.executemany(
                "INSERT INTO profile_bank_sources (profile_id,news_id,passage_id) VALUES (?,?,?)",
                [(profile_id, news.id, item["passage_id"]) for item in evidence],
            )


def refresh_all_recommendations() -> dict:
    from app.corpus.search import load_news

    news = load_news()
    with connect_app() as connection:
        profiles = [row[0] for row in connection.execute("SELECT id FROM business_profiles")]
    for profile_id in profiles:
        ensure_recommendations(profile_id, news)
    with connect_app() as connection:
        matched = connection.execute(
            "SELECT COUNT(*) FROM profile_bank_recommendations WHERE origin='corpus' AND text<>''"
        ).fetchone()[0]
    return {"profiles": len(profiles), "news": len(news), "matched": matched}


def selected_evidence(saved: dict, news) -> list[dict]:
    """Use the persisted source decision, including a model's explicit rejection."""
    ensure_recommendations(saved["profile_id"], [news])
    with connect_app() as connection:
        row = connection.execute(
            "SELECT origin,sources_json FROM profile_bank_recommendations WHERE profile_id=? AND news_id=?",
            (saved["profile_id"], news.id),
        ).fetchone()
    if row is not None and row["origin"] == "corpus":
        return json.loads(row["sources_json"])
    return bank_evidence(saved["profile"], news)[0]


def context_hash(evidence: list[dict]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def evidence_citations(items: list[dict]) -> list[dict]:
    return [{"title": item["title"], "url": "", "published_at": item["published_at"], "kind": "bank"}
            for item in items]
