import hashlib
from datetime import datetime, timezone

import yaml

from app.corpus.search import NewsItem, classify
from app.db import ROOT, connect, init_db
from app.ingest.news import clean_article


def remember_pasted(
    title: str,
    text: str,
    url: str = "",
    published_at: str = "",
    source: str = "",
) -> NewsItem:
    article = clean_article(title, text, url, published_at, source)
    event_type = classify(f"{article.title} {article.text}")
    taxonomy = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))
    spec = taxonomy["event_types"].get(event_type or "", {})
    news_id = "pasted-" + hashlib.sha256(f"{article.title}\n{article.text}".encode("utf-8")).hexdigest()[:12]
    item = NewsItem(
        id=news_id,
        url=article.url,
        title=article.title,
        published_at=article.published_at,
        excerpt=article.text,
        event_type=event_type,
        event_title=spec.get("title"),
        source=article.source,
        status="pasted",
    )
    init_db()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO pasted_news (id, title, excerpt, url, event_type, event_title, published_at, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                excerpt = excluded.excerpt,
                url = excluded.url,
                event_type = excluded.event_type,
                event_title = excluded.event_title,
                published_at = excluded.published_at,
                source = excluded.source
            """,
            (
                item.id,
                item.title,
                item.excerpt,
                item.url,
                item.event_type,
                item.event_title,
                item.published_at,
                item.source,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return item


def pasted_by_id(event_id: str) -> NewsItem | None:
    init_db()
    with connect() as connection:
        row = connection.execute("SELECT * FROM pasted_news WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        return None
    return NewsItem(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        published_at=row["published_at"],
        excerpt=row["excerpt"],
        event_type=row["event_type"],
        event_title=row["event_title"],
        source=row["source"] or None,
        status="pasted",
    )


def load_pasted_news() -> list[NewsItem]:
    init_db()
    with connect() as connection:
        ids = [row["id"] for row in connection.execute("SELECT id FROM pasted_news ORDER BY created_at, id")]
    return [item for event_id in ids if (item := pasted_by_id(event_id)) is not None]
