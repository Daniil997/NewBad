"""SQLite persistence for collected news."""

import json
from datetime import date, timedelta

from app.news_topics import news_topic
from app.user_store import app_db_path, connect_app, init_app_db, now_utc


def imported_news() -> list[dict]:
    if not app_db_path().is_file():
        return []
    init_app_db()
    with connect_app() as connection:
        rows = connection.execute(
            "SELECT id, source_id, source, url, title, published_at, text, loaded_at, sha256, event_type, event_title "
            "FROM news_articles ORDER BY published_at DESC, loaded_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def recommendation_profiles() -> list[dict]:
    """Real company profiles available to the developer's editorial workspace."""
    init_app_db()
    with connect_app() as connection:
        rows = connection.execute(
            "SELECT p.id, p.profile_json, p.status, p.profile_version, u.login FROM business_profiles p "
            "JOIN users u ON u.id = p.owner_id WHERE u.status = 'active' ORDER BY u.login, p.id"
        ).fetchall()
    profiles = []
    for row in rows:
        profile = json.loads(row["profile_json"])
        profiles.append({"id": row["id"], "login": row["login"], "status": row["status"],
                         "profile_version": row["profile_version"],
                         **{key: str(profile.get(key) or "") for key in
                            ("company_name", "industry", "region", "city", "description", "products_services")}})
    return profiles


def bank_recommendations(profile_id: str) -> dict[str, dict]:
    """Only the selected company's editorial assessments, never another company's or legacy shared text."""
    init_app_db()
    with connect_app() as connection:
        rows = connection.execute(
            "SELECT news_id, text, page, updated_at, origin, sources_json, match_reason "
            "FROM profile_bank_recommendations WHERE profile_id = ? AND text<>''",
            (profile_id,),
        ).fetchall()
    return {row["news_id"]: {
        **{key: row[key] for key in ("text", "page", "updated_at")},
        **({"origin": "corpus", "sources": json.loads(row["sources_json"]), "match_reason": row["match_reason"]}
           if row["origin"] == "corpus" else {}),
    } for row in rows}


def recommendation_news(profile_id: str) -> list[dict] | None:
    init_app_db()
    from app.bank_store import ensure_recommendations

    ensure_recommendations(profile_id)
    with connect_app() as connection:
        if not connection.execute("SELECT id FROM business_profiles WHERE id = ?", (profile_id,)).fetchone():
            return None
        rows = connection.execute(
            "SELECT n.id, n.title, n.source, n.url, n.published_at, n.event_title, "
            "COALESCE(r.text, '') AS text, r.page, r.updated_at, "
            "n.bank_recommendation AS legacy_text, n.bank_recommendation_page AS legacy_page "
            "FROM news_articles n LEFT JOIN profile_bank_recommendations r "
            "ON r.news_id = n.id AND r.profile_id = ? ORDER BY n.published_at DESC, n.id DESC",
            (profile_id,),
        ).fetchall()
    assessments = bank_recommendations(profile_id)
    return [{**{key: row[key] for key in ("id", "title", "source", "url", "published_at", "event_title")},
             "bank_recommendation": assessments.get(row["id"], {"text": "", "page": None, "updated_at": None}),
             "legacy_bank_recommendation": {"text": row["legacy_text"], "page": row["legacy_page"]}}
            for row in rows]


def save_bank_recommendation(profile_id: str, news_id: str, text: str, page: int | None) -> dict | None:
    init_app_db()
    text = text.strip()
    with connect_app() as connection:
        connection.execute("BEGIN IMMEDIATE")
        # Check both foreign keys explicitly so deleted profiles/news produce a useful 404.
        if not connection.execute("SELECT id FROM business_profiles WHERE id = ?", (profile_id,)).fetchone():
            return None
        if not connection.execute("SELECT id FROM news_articles WHERE id = ?", (news_id,)).fetchone():
            return None
        if not text:
            connection.execute("DELETE FROM profile_bank_recommendations WHERE profile_id = ? AND news_id = ?",
                               (profile_id, news_id))
            return {"text": "", "page": None, "updated_at": None}
        updated_at = now_utc()
        connection.execute(
            "INSERT INTO profile_bank_recommendations (profile_id, news_id, text, page, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(profile_id, news_id) DO UPDATE SET "
            "text = excluded.text, page = excluded.page, updated_at = excluded.updated_at, "
            "origin='editorial',sources_json='[]',match_reason='',profile_version=NULL,news_hash=NULL,corpus_version=NULL",
            (profile_id, news_id, text, page, updated_at),
        )
        connection.execute("DELETE FROM profile_bank_sources WHERE profile_id=? AND news_id=?", (profile_id, news_id))
    return {"text": text, "page": page, "updated_at": updated_at}


def refresh_news_topics() -> int:
    """Reclassify existing articles after taxonomy changes; preserve source text and IDs."""
    from app.news_topics import load_taxonomy

    taxonomy = load_taxonomy()
    with connect_app() as connection:
        rows = connection.execute("SELECT id,title,text,event_type,event_title FROM news_articles").fetchall()
        changes = [(*news_topic(row["title"], row["text"], taxonomy), row["id"]) for row in rows
                   if news_topic(row["title"], row["text"], taxonomy) != (row["event_type"], row["event_title"])]
        connection.executemany("UPDATE news_articles SET event_type=?,event_title=? WHERE id=?", changes)
    return len(changes)


def store_imported_news(source_id: str, items: list[dict]) -> int:
    if not items:
        return 0
    init_app_db()
    added = 0
    with connect_app() as connection:
        for item in items:
            existing = connection.execute(
                "SELECT id, text FROM news_articles WHERE url = ?", (item["url"],)
            ).fetchone()
            if existing:
                # Preserve the stable record ID. Replace known CBR section-page contamination
                # even when the clean RSS summary is shorter than the previously stored page.
                existing_text = existing["text"].casefold()
                contaminated = (
                    "страница была полезной?" in existing_text
                    and "последнее обновление страницы" in existing_text
                )
                if len(item["text"]) > len(existing["text"]) or contaminated:
                    connection.execute(
                        "UPDATE news_articles SET text = ?, sha256 = ?, loaded_at = ? WHERE id = ?",
                        (item["text"], item["sha256"], item["loaded_at"], existing["id"]),
                    )
                continue
            event_type, event_title = item.get("event_type"), item.get("event_title")
            if not event_type or not event_title:
                event_type, event_title = news_topic(item["title"], item["text"])
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO news_articles (
                    id, source_id, source, url, title, published_at, text, loaded_at, sha256, created_at,
                    event_type, event_title
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"], source_id, item["source"], item["url"], item["title"], item["published_at"],
                    item["text"], item["loaded_at"], item["sha256"], now_utc(), event_type, event_title,
                ),
            )
            added += cursor.rowcount
    return added


def prune_stored_news(*, retention_days: int, max_items: int) -> dict[str, int]:
    if not app_db_path().is_file():
        return {"removed_by_age": 0, "removed_by_count": 0, "remaining": 0}
    init_app_db()
    cutoff = (date.today() - timedelta(days=retention_days)).isoformat()
    with connect_app() as connection:
        age_cursor = connection.execute("DELETE FROM news_articles WHERE published_at < ?", (cutoff,))
        excess = connection.execute(
            "SELECT id FROM news_articles ORDER BY published_at DESC, loaded_at DESC, id DESC LIMIT -1 OFFSET ?",
            (max_items,),
        ).fetchall()
        if excess:
            connection.executemany("DELETE FROM news_articles WHERE id = ?", [(row["id"],) for row in excess])
        remaining = int(connection.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0])
    return {"removed_by_age": age_cursor.rowcount, "removed_by_count": len(excess), "remaining": remaining}


def clear_stored_news() -> int:
    """Remove all collected news records without touching profiles or other app data."""
    if not app_db_path().is_file():
        return 0
    init_app_db()
    with connect_app() as connection:
        count = int(connection.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0])
        connection.execute("DELETE FROM news_articles")
    return count


def stored_news_count() -> int:
    if not app_db_path().is_file():
        return 0
    init_app_db()
    with connect_app() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0])


def recent_news_count(lookback_days: int) -> int:
    if not app_db_path().is_file():
        return 0
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with connect_app() as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM news_articles WHERE published_at BETWEEN ? AND ?", (cutoff, tomorrow),
        ).fetchone()[0])
