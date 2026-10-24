"""SQLite persistence for collected news; bundled demo fixtures remain JSON files."""

from datetime import date, timedelta

from app.user_store import app_db_path, connect_app, init_app_db, now_utc


def imported_news() -> list[dict]:
    if not app_db_path().is_file():
        return []
    init_app_db()
    with connect_app() as connection:
        rows = connection.execute(
            "SELECT id, source_id, source, url, title, published_at, text, loaded_at, sha256 "
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
                # Preserve the stable record ID but enrich its body if the extractor found more content.
                if len(item["text"]) > len(existing["text"]):
                    connection.execute(
                        "UPDATE news_articles SET text = ?, sha256 = ?, loaded_at = ? WHERE id = ?",
                        (item["text"], item["sha256"], item["loaded_at"], existing["id"]),
                    )
                continue
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO news_articles (
                    id, source_id, source, url, title, published_at, text, loaded_at, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"], source_id, item["source"], item["url"], item["title"], item["published_at"],
                    item["text"], item["loaded_at"], item["sha256"], now_utc(),
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


def stored_news_count() -> int:
    if not app_db_path().is_file():
        return 0
    init_app_db()
    with connect_app() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0])
