"""SQLite persistence for collected news; bundled demo fixtures remain JSON files."""

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
