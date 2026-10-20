import hashlib
from datetime import date, datetime, timezone

import yaml

from app.corpus.search import NewsItem, classify
from app.db import ROOT, connect, init_db


def remember_pasted(title: str, text: str, url: str = "") -> NewsItem:
    cleaned_title = title.strip()
    cleaned_text = text.strip()
    if not cleaned_title or not cleaned_text:
        raise ValueError("Нужны заголовок и текст новости")
    event_type = classify(f"{cleaned_title} {cleaned_text}")
    taxonomy = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))
    spec = taxonomy["event_types"].get(event_type or "", {})
    news_id = "pasted-" + hashlib.sha256(f"{cleaned_title}\n{cleaned_text}".encode("utf-8")).hexdigest()[:12]
    item = NewsItem(
        id=news_id,
        url=url.strip(),
        title=cleaned_title,
        published_at=date.today().isoformat(),
        excerpt=cleaned_text,
        event_type=event_type,
        event_title=spec.get("title"),
    )
    init_db()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO pasted_news (id, title, excerpt, url, event_type, event_title, published_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                excerpt = excluded.excerpt,
                url = excluded.url,
                event_type = excluded.event_type,
                event_title = excluded.event_title
            """,
            (
                item.id,
                item.title,
                item.excerpt,
                item.url,
                item.event_type,
                item.event_title,
                item.published_at,
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
    )
