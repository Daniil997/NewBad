"""Импорт одной RSS-ленты. Пустая лента — не ошибка источника. Пропущенные поля не заполняются."""

import hashlib
import html
import logging
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx
import yaml
from pydantic import BaseModel

from app.db import ROOT

logger = logging.getLogger(__name__)

_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_TAG = re.compile(r"(?is)<(script|style).*?>.*?</\1>|<[^>]+>")
_SPACE = re.compile(r"\s+")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXCERPT = 600
_MIN_TEXT = 40
_SIMILAR = 0.8
_fetched_at: dict[str, float] = {}


class NewsIngestError(Exception):
    """source — лента недоступна или не разбирается, timeout — нет ответа, rate — слишком частый запрос."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


class NewsSource(BaseModel):
    id: str
    name: str
    url: str
    timeout_s: float = 15
    min_interval_s: float = 30


class ImportedNews(BaseModel):
    id: str
    url: str
    title: str
    published_at: str
    text: str
    source: str
    loaded_at: str
    sha256: str
    status: str
    duplicate_of: str | None = None


class RejectedNews(BaseModel):
    url: str | None = None
    title: str | None = None
    reason: str


class NewsImport(BaseModel):
    source_id: str
    source: str
    status: str
    loaded_at: str
    items: list[ImportedNews]
    rejected: list[RejectedNews]


class CatalogImport(BaseModel):
    status: str
    started_at: str
    finished_at: str
    sources: list[dict]


def import_news(
    output: Path,
    *,
    source: NewsSource | None = None,
    fetch: Callable[[], str] | None = None,
    loaded_on: date | None = None,
    min_interval_s: float | None = None,
    max_age_days: int | None = None,
    known_items: list[ImportedNews] | None = None,
    persist_files: bool = True,
) -> NewsImport:
    """Читает ленту, нормализует записи и пишет только принятые новости. Повтор URL или похожий заголовок — дубль."""
    origin = source or load_news_source()
    interval = origin.min_interval_s if min_interval_s is None else min_interval_s
    _allow(origin.id, interval)
    try:
        payload = _fetch_rss(origin.url, origin.timeout_s) if fetch is None else fetch()
        raw_items = _items(payload)
    except NewsIngestError:
        raise
    except httpx.TimeoutException as error:
        raise NewsIngestError("timeout", "Источник не ответил вовремя") from error
    except httpx.HTTPError as error:
        raise NewsIngestError("source", f"Источник недоступен: {error.__class__.__name__}") from error
    except ElementTree.ParseError as error:
        raise NewsIngestError("source", "Лента не разбирается как RSS") from error
    loaded_at = (loaded_on or date.today()).isoformat()
    known = [*_known(output), *(known_items or [])] if persist_files else list(known_items or [])
    accepted: list[ImportedNews] = []
    rejected: list[RejectedNews] = []
    for raw in raw_items:
        item, reason = _normalize(raw, origin, loaded_at)
        if item is None and reason is not None:
            rejected.append(reason)
            continue
        if max_age_days is not None and (loaded_on or date.today()) - date.fromisoformat(item.published_at) > timedelta(days=max_age_days):
            rejected.append(RejectedNews(url=item.url, title=item.title, reason="старше окна сбора"))
            continue
        earlier = _duplicate(item, [*known, *accepted])
        if earlier is not None:
            accepted.append(item.model_copy(update={"status": "duplicate", "duplicate_of": earlier}))
            continue
        accepted.append(item)
        known.append(item)
    batch = NewsImport(
        source_id=origin.id,
        source=origin.name,
        status=_batch_status(raw_items, accepted),
        loaded_at=loaded_at,
        items=accepted,
        rejected=rejected,
    )
    if persist_files:
        _write(output, batch)
    logger.info(
        "news-import source=%s status=%s imported=%s rejected=%s",
        origin.id,
        batch.status,
        sum(item.status == "imported" for item in accepted),
        len(rejected),
    )
    return batch


class CleanArticle(BaseModel):
    title: str
    text: str
    url: str
    published_at: str
    source: str | None


def clean_article(
    title: str,
    text: str,
    url: str = "",
    published_at: str = "",
    source: str = "",
) -> CleanArticle:
    """Та же очистка, что у RSS. Пустые адрес, дата и источник остаются пустыми."""
    cleaned_title = _plain(title)
    cleaned_text = _excerpt(_plain(text))
    if not cleaned_title:
        raise ValueError("Нужен заголовок новости")
    if not cleaned_text:
        raise ValueError("Текст новости пустой")
    if len(cleaned_text) < _MIN_TEXT:
        raise ValueError("Текст новости слишком короткий")
    cleaned_url = ""
    if url.strip():
        parsed = _url(url)
        if parsed is None:
            raise ValueError("Некорректный URL")
        cleaned_url = parsed
    cleaned_date = ""
    if published_at.strip():
        parsed_date = _published(published_at)
        if parsed_date is None:
            raise ValueError("Некорректная дата публикации")
        cleaned_date = parsed_date
    cleaned_source = _plain(source)
    return CleanArticle(
        title=cleaned_title,
        text=cleaned_text,
        url=cleaned_url,
        published_at=cleaned_date,
        source=cleaned_source or None,
    )


def load_news_source(path: Path | None = None) -> NewsSource:
    catalog = path or (ROOT / "config" / "news_sources.yaml")
    payload = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    if payload.get("sources"):
        return NewsSource.model_validate(payload["sources"][0])
    return NewsSource.model_validate(payload["source"])


def load_news_sources(path: Path | None = None) -> list[NewsSource]:
    catalog = path or (ROOT / "config" / "news_sources.yaml")
    payload = yaml.safe_load(catalog.read_text(encoding="utf-8")) or {}
    raw_sources = payload.get("sources") or ([payload["source"]] if payload.get("source") else [])
    sources = [NewsSource.model_validate(item) for item in raw_sources]
    if not sources or len({item.id for item in sources}) != len(sources):
        raise ValueError("Каталог новостных источников пуст или содержит повторяющиеся ID")
    for source in sources:
        parsed = urlsplit(source.url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"Источник {source.id} должен использовать HTTPS")
    return sources


def import_news_catalog(output: Path, *, max_age_days: int = 3) -> CatalogImport:
    from app.news_store import imported_news, store_imported_news

    started = datetime.now(timezone.utc).isoformat()
    results = []
    known = [ImportedNews.model_validate({**item, "status": "imported"}) for item in imported_news()]
    for source in load_news_sources():
        try:
            batch = import_news(
                output, source=source, max_age_days=max_age_days,
                known_items=known, persist_files=False,
            )
            new_items = [item for item in batch.items if item.status == "imported"]
            store_imported_news(source.id, [item.model_dump() for item in new_items])
            known.extend(new_items)
            results.append({
                "source_id": source.id, "source": source.name, "status": batch.status,
                "imported": sum(item.status == "imported" for item in batch.items),
                "duplicates": sum(item.status == "duplicate" for item in batch.items),
                "rejected": len(batch.rejected), "error": "",
            })
        except NewsIngestError as error:
            results.append({
                "source_id": source.id, "source": source.name, "status": "error",
                "imported": 0, "duplicates": 0, "rejected": 0, "error": error.kind,
            })
    statuses = [item["status"] for item in results]
    status = "error" if statuses and all(item == "error" for item in statuses) else "partial" if "error" in statuses else "complete"
    return CatalogImport(status=status, started_at=started, finished_at=datetime.now(timezone.utc).isoformat(), sources=results)


def prune_imported_news(output: Path, *, retention_days: int, max_items: int) -> dict[str, int]:
    cutoff = date.today() - timedelta(days=retention_days)
    items: list[tuple[Path, str, str]] = []
    expired = 0
    if not output.is_dir():
        return {"removed_by_age": 0, "removed_by_count": 0, "remaining": 0}
    for path in output.glob("*.json"):
        if path.name.startswith("import"):
            continue
        try:
            item = ImportedNews.model_validate_json(path.read_text(encoding="utf-8"))
            published = date.fromisoformat(item.published_at)
        except (OSError, ValueError):
            continue
        if published < cutoff:
            path.unlink(missing_ok=True)
            expired += 1
        else:
            items.append((path, item.published_at, item.loaded_at))
    items.sort(key=lambda item: (item[1], item[2], item[0].name), reverse=True)
    excess = items[max_items:]
    for path, _published, _loaded in excess:
        path.unlink(missing_ok=True)
    return {
        "removed_by_age": expired,
        "removed_by_count": len(excess),
        "remaining": len(items) - len(excess),
    }


def migrate_legacy_imports(output: Path) -> int:
    """Move old JSON imports into the application SQLite store without losing records."""
    from app.news_store import imported_news, store_imported_news

    moved = 0
    known = imported_news()
    known_ids = {item["id"] for item in known}
    known_urls = {item["url"] for item in known}
    if not output.is_dir():
        return moved
    for path in output.glob("*.json"):
        if path.name.startswith("import"):
            continue
        try:
            item = ImportedNews.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        source_id = "cbr_press" if "банк россии" in item.source.casefold() else "legacy"
        added = store_imported_news(source_id, [item.model_dump()])
        if added or item.id in known_ids or item.url in known_urls:
            path.unlink(missing_ok=True)
            known_ids.add(item.id)
            known_urls.add(item.url)
            moved += 1
    return moved


def _fetch_rss(url: str, timeout_s: float) -> str:
    for attempt in range(3):
        try:
            response = httpx.get(url, timeout=timeout_s, follow_redirects=False, headers={"User-Agent": "NewBad/0.1"})
            response.raise_for_status()
            if len(response.content) > 2 * 1024 * 1024:
                raise NewsIngestError("source", "RSS-лента превышает лимит 2 МиБ")
            return response.text
        except httpx.TimeoutException as error:
            if attempt == 2:
                raise NewsIngestError("timeout", "Источник не ответил после повторных попыток") from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code < 500 or attempt == 2:
                raise NewsIngestError("source", f"Источник вернул HTTP {error.response.status_code}") from error
        except httpx.HTTPError as error:
            if attempt == 2:
                raise NewsIngestError("source", f"Источник недоступен: {error.__class__.__name__}") from error
        time.sleep(0.25 * (2 ** attempt))
    raise NewsIngestError("source", "Источник недоступен")


def _items(payload: str) -> list[ElementTree.Element]:
    root = ElementTree.fromstring(payload.lstrip("\ufeff"))
    channel = root.find("channel")
    if channel is None:
        raise NewsIngestError("source", "В ленте нет канала")
    return list(channel.findall("item"))


def _normalize(
    raw: ElementTree.Element,
    source: NewsSource,
    loaded_at: str,
) -> tuple[ImportedNews | None, RejectedNews | None]:
    title = _plain(_child(raw, "title"))
    url = _url(_child(raw, "link"))
    published = _published(_child(raw, "pubDate"))
    text = _excerpt(_plain(_child(raw, "description")))
    if len(text) < _MIN_TEXT and len(title) >= _MIN_TEXT:
        text = _excerpt(title)
    if not title:
        return None, RejectedNews(url=url, title=None, reason="нет заголовка")
    if url is None:
        return None, RejectedNews(url=None, title=title, reason="некорректный URL")
    if published is None:
        return None, RejectedNews(url=url, title=title, reason="нет или некорректна дата")
    if not text:
        return None, RejectedNews(url=url, title=title, reason="пустой текст")
    identifier = _identifier(_child(raw, "guid"), url, source.id)
    item = ImportedNews(
        id=identifier,
        url=url,
        title=title,
        published_at=published,
        text=text,
        source=source.name,
        loaded_at=loaded_at,
        sha256=_checksum(title, url, published, text, source.name),
        status="imported",
    )
    return item, None


def _duplicate(item: ImportedNews, known: list[ImportedNews]) -> str | None:
    for earlier in known:
        if earlier.status != "imported":
            continue
        if _same_url(earlier.url, item.url) or earlier.id == item.id or _similar(earlier.title, item.title):
            return earlier.id
    return None


def _known(output: Path) -> list[ImportedNews]:
    if not output.is_dir():
        return []
    items: list[ImportedNews] = []
    for path in sorted(output.glob("*.json")):
        if path.name.startswith("import"):
            continue
        items.append(ImportedNews.model_validate_json(path.read_text(encoding="utf-8")))
    return items


def _write(output: Path, batch: NewsImport) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for item in batch.items:
        if item.status != "imported":
            continue
        (output / f"{item.id}.json").write_text(item.model_dump_json(indent=2), encoding="utf-8")
    (output / "import.json").write_text(batch.model_dump_json(indent=2), encoding="utf-8")
    (output / f"import-{batch.source_id}.json").write_text(batch.model_dump_json(indent=2), encoding="utf-8")


def _batch_status(raw_items: list, accepted: list[ImportedNews]) -> str:
    if not raw_items:
        return "empty"
    if any(item.status == "imported" for item in accepted):
        return "imported"
    if accepted:
        return "duplicate"
    return "rejected"


def _allow(source_id: str, interval: float) -> None:
    now = time.monotonic()
    previous = _fetched_at.get(source_id)
    if interval > 0 and previous is not None and now - previous < interval:
        raise NewsIngestError("rate", "Слишком частый запрос к источнику")
    _fetched_at[source_id] = now


def _plain(value: str) -> str:
    text = html.unescape(_TAG.sub(" ", value))
    return _SPACE.sub(" ", text).strip()


def _excerpt(text: str) -> str:
    if len(text) <= _EXCERPT:
        return text
    clipped = text[:_EXCERPT].rsplit(" ", 1)[0]
    return clipped or text[:_EXCERPT]


def _url(value: str) -> str | None:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value.strip()


def _published(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    if _DATE.fullmatch(raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return None
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if moment is None:
        return None
    return moment.date().isoformat()


def _identifier(guid: str, url: str, source_id: str = "cbr_press") -> str:
    raw = guid.strip() or hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    safe = re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-")
    prefix = re.sub(r"[^a-z0-9-]+", "-", source_id.lower().replace("_", "-"))
    return f"{prefix}-{safe or 'item'}"


def _checksum(title: str, url: str, published: str, text: str, source: str) -> str:
    payload = "\n".join([title, url, published, text, source])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _same_url(left: str, right: str) -> bool:
    return left.rstrip("/") == right.rstrip("/")


def _similar(left: str, right: str) -> bool:
    first = set(_TOKEN.findall(left.lower().replace("ё", "е")))
    second = set(_TOKEN.findall(right.lower().replace("ё", "е")))
    if len(first) < 3 or len(second) < 3:
        return False
    union = first | second
    return bool(union) and len(first & second) / len(union) >= _SIMILAR


def _child(raw: ElementTree.Element, name: str) -> str:
    found = raw.find(name)
    return "" if found is None or found.text is None else found.text
