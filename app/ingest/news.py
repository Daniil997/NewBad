"""Импорт одной RSS-ленты. Пустая лента — не ошибка источника. Пропущенные поля не заполняются."""

import hashlib
import html
import logging
from html.parser import HTMLParser
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
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
_MAX_ARTICLE_TEXT = 20000
_MAX_ARTICLE_BYTES = 2 * 1024 * 1024
_MAX_ARTICLE_FETCHES = 60
_MIN_TEXT = 40
_MIN_ARTICLE_TEXT = 200
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
    article_mode: str = "rss_only"


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
    seen: int = 0
    article_fetches: int = 0


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
    fetch_article: Callable[[str], str | None] | None = None,
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
    article_fetches = 0
    current_day = loaded_on or date.today()
    # Some feeds list hundreds of old messages before the newest. Reserve full
    # article requests for valid, recent entries instead of spending the whole
    # budget on stale RSS links.
    by_recent_date = sorted(
        raw_items,
        key=lambda raw: _published(
            _child_local(raw, "pubDate") or _child_local(raw, "published")
            or _child_local(raw, "updated") or _child_local(raw, "date")
        ) or "",
        reverse=True,
    )
    for raw in by_recent_date:
        published = _published(
            _child_local(raw, "pubDate") or _child_local(raw, "published")
            or _child_local(raw, "updated") or _child_local(raw, "date")
        )
        item_url = _item_url(raw)
        if published is not None and max_age_days is not None:
            delta = (current_day - date.fromisoformat(published)).days
            if delta > max_age_days:
                rejected.append(RejectedNews(
                    url=item_url, title=_plain(_child_local(raw, "title")), reason="старше окна сбора",
                ))
                continue
            if delta < -1:
                rejected.append(RejectedNews(
                    url=item_url, title=_plain(_child_local(raw, "title")), reason="дата публикации в будущем",
                ))
                continue
        article_fetcher = None
        if (
            origin.article_mode == "html"
            and article_fetches < _MAX_ARTICLE_FETCHES
            and item_url is not None
            and published is not None
        ):
            # Long content:encoded bodies are already full enough; do not waste
            # network calls on them unless an explicit fetcher was supplied.
            rss_body = _html_to_text(
                _child_local(raw, "encoded") or _child_local(raw, "content")
                or _child_local(raw, "description") or _child_local(raw, "summary")
            )
            if fetch_article is not None or len(rss_body) < 1600:
                article_fetcher = fetch_article or (lambda url: _fetch_article(url, origin, origin.timeout_s))
                article_fetches += 1
        item, reason = _normalize(raw, origin, loaded_at, fetch_article=article_fetcher)
        if item is None and reason is not None:
            rejected.append(reason)
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
        status=_batch_status(raw_items, accepted, rejected),
        loaded_at=loaded_at,
        items=accepted,
        rejected=rejected,
        seen=len(raw_items),
        article_fetches=article_fetches,
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
            # Store duplicates too: the same article may now have a longer, better-extracted body.
            store_imported_news(source.id, [item.model_dump() for item in batch.items])
            known.extend(new_items)
            rejected_content = [
                row for row in batch.rejected
                if row.reason not in {"старше окна сбора", "дата публикации в будущем"}
            ]
            skipped_old = sum(row.reason == "старше окна сбора" for row in batch.rejected)
            skipped_future = sum(row.reason == "дата публикации в будущем" for row in batch.rejected)
            rejection_reasons: dict[str, int] = {}
            for rejected_item in rejected_content:
                rejection_reasons[rejected_item.reason] = rejection_reasons.get(rejected_item.reason, 0) + 1
            rejected_examples = [
                {"title": item.title or "", "url": item.url or "", "reason": item.reason}
                for item in rejected_content[:5]
            ]
            results.append({
                "source_id": source.id, "source": source.name, "status": batch.status,
                "seen": batch.seen, "article_fetches": batch.article_fetches,
                "imported": sum(item.status == "imported" for item in batch.items),
                "duplicates": sum(item.status == "duplicate" for item in batch.items),
                "rejected": len(rejected_content), "skipped_old": skipped_old,
                "skipped_future": skipped_future, "rejection_reasons": rejection_reasons,
                "rejected_examples": rejected_examples, "error": "",
            })
        except NewsIngestError as error:
            results.append({
                "source_id": source.id, "source": source.name, "status": "error",
                "imported": 0, "duplicates": 0, "rejected": 0,
                "seen": 0, "article_fetches": 0, "skipped_old": 0, "skipped_future": 0,
                "error": error.kind,
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


def _get_official_page(url: str, timeout_s: float, headers: dict[str, str]) -> httpx.Response:
    """Follow only bounded same-site HTTPS redirects from official sources."""
    original = urlsplit(url)
    root_host = (original.hostname or "").casefold().removeprefix("www.")
    allowed = {root_host, f"www.{root_host}"}
    current_url = url
    for _ in range(4):
        response = httpx.get(
            current_url, timeout=timeout_s, follow_redirects=False, headers=headers,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("location", "")
        if not location:
            raise ValueError("Перенаправление без адреса")
        destination = urljoin(current_url, location)
        target = urlsplit(destination)
        if (
            target.scheme != "https" or (target.hostname or "").casefold() not in allowed
            or target.username or target.password or target.port not in (None, 443)
        ):
            raise ValueError("Перенаправление на другой или небезопасный сайт")
        current_url = destination
    raise ValueError("Превышен лимит перенаправлений официального источника")


def _fetch_rss(url: str, timeout_s: float) -> str:
    for attempt in range(3):
        try:
            response = _get_official_page(url, timeout_s, {"User-Agent": "NewBad/0.1"})
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
        except ValueError as error:
            raise NewsIngestError("source", f"Некорректное перенаправление RSS: {error}") from error
        time.sleep(0.25 * (2 ** attempt))
    raise NewsIngestError("source", "Источник недоступен")


def _items(payload: str) -> list[ElementTree.Element]:
    root = ElementTree.fromstring(payload.lstrip("\ufeff"))
    local_name = lambda node: node.tag.rsplit("}", 1)[-1].casefold()
    if local_name(root) == "feed":
        return [node for node in root if local_name(node) == "entry"]
    channel = next((node for node in root if local_name(node) == "channel"), None)
    if channel is None:
        raise NewsIngestError("source", "В ленте нет канала")
    return [node for node in channel if local_name(node) == "item"]


def _normalize(
    raw: ElementTree.Element,
    source: NewsSource,
    loaded_at: str,
    *,
    fetch_article: Callable[[str], str | None] | None = None,
) -> tuple[ImportedNews | None, RejectedNews | None]:
    title = _plain(_child_local(raw, "title"))
    url = _item_url(raw)
    published = _published(
        _child_local(raw, "pubDate") or _child_local(raw, "published")
        or _child_local(raw, "updated") or _child_local(raw, "date")
    )
    # RSS 2.0 часто содержит только короткий description; content:encoded может содержать полный текст.
    rss_text = _html_to_text(
        _child_local(raw, "encoded") or _child_local(raw, "content")
        or _child_local(raw, "description") or _child_local(raw, "summary")
    )
    text = rss_text
    if url and fetch_article:
        try:
            full_text = _html_to_text(fetch_article(url) or "")
            if len(full_text) > len(text):
                text = full_text
        except (httpx.HTTPError, ValueError, UnicodeError) as error:
            logger.info("news-article-extract-failed source=%s url=%s error=%s", source.id, url, error.__class__.__name__)
    text = _limit_article_text(text)
    if not title:
        return None, RejectedNews(url=url, title=None, reason="нет заголовка")
    if url is None:
        return None, RejectedNews(url=None, title=title, reason="некорректный URL")
    if published is None:
        return None, RejectedNews(url=url, title=title, reason="нет или некорректна дата")
    if len(text) < _MIN_TEXT:
        return None, RejectedNews(url=url, title=title, reason="текст новости отсутствует или слишком короткий")
    # Заголовок больше не подменяет содержание: это создавало фиктивные «новости» без фактов.
    identifier = _identifier(_child_local(raw, "guid") or _child_local(raw, "id"), url, source.id)
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
        if (_same_url(earlier.url, item.url) or earlier.id == item.id
                or (
                    earlier.published_at == item.published_at
                    and _similar(earlier.title, item.title)
                    and _SPACE.sub(" ", earlier.text).casefold() == _SPACE.sub(" ", item.text).casefold()
                )):
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


def _batch_status(
    raw_items: list, accepted: list[ImportedNews], rejected: list[RejectedNews] | None = None,
) -> str:
    if not raw_items:
        return "empty"
    if any(item.status == "imported" for item in accepted):
        return "imported"
    if accepted:
        return "duplicate"
    if rejected and all(item.reason == "старше окна сбора" for item in rejected):
        return "outside_window"
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
    # RSS extensions may publish W3C/ISO timestamps rather than RFC-2822 pubDate.
    if "T" in raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
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


def _item_url(raw: ElementTree.Element) -> str | None:
    """RSS text link or Atom's <link href=...>; never invent a source address."""
    value = _child_local(raw, "link")
    if value:
        return _url(value)
    for node in raw:
        if node.tag.rsplit("}", 1)[-1].casefold() == "link":
            href = node.attrib.get("href", "")
            if href and node.attrib.get("rel", "alternate") == "alternate":
                return _url(href)
    return None


def _child(raw: ElementTree.Element, name: str) -> str:
    found = raw.find(name)
    return "" if found is None or found.text is None else found.text


def _child_local(raw: ElementTree.Element, name: str) -> str:
    """Find RSS fields even when they use XML namespaces such as content:encoded or dc:date."""
    for node in raw.iter():
        if node.tag.rsplit("}", 1)[-1].split(":")[-1].casefold() == name.casefold():
            return "".join(node.itertext()).strip()
    return ""


class _ArticleTextParser(HTMLParser):
    """Extract readable article text while suppressing navigation and boilerplate."""

    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "button", "iframe", "template", "select", "option"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "section", "article", "main", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.body: list[str] = []
        self.stack: list[tuple[str, bool, int | None]] = []
        self.active: list[int] = []
        self.candidates: list[tuple[int, list[str]]] = []
        self.anchor_depth = 0
        self.anchor_count = 0
        self.anchor_chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.casefold(): (value or "").casefold() for key, value in attrs}
        parent_skipped = bool(self.stack and self.stack[-1][1])
        skipped = parent_skipped or tag in self._SKIP
        candidate_id = None
        if not skipped:
            marker = f"{attrs_map.get('id', '')} {attrs_map.get('class', '')}"
            score = 100 if tag == "article" else 80 if tag == "main" else 0
            if re.search(r"article[-_ ]?(body|content|text)|news[-_ ]?(body|content|text)|press[-_ ]?(body|content|text)|detail[-_ ]?content", marker):
                score = max(score, 90)
            elif re.search(r"content|article|news|press|detail|story", marker):
                score = max(score, 45)
            if score:
                candidate_id = len(self.candidates)
                self.candidates.append((score, []))
                self.active.append(candidate_id)
        self.stack.append((tag, skipped, candidate_id))
        if not skipped and tag == "a":
            self.anchor_depth += 1
            self.anchor_count += 1
        if not skipped and tag in self._BLOCK:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        match = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if match is None:
            return
        popped = self.stack[match:]
        del self.stack[match:]
        for _tag, _skipped, candidate_id in reversed(popped):
            if _tag == "a":
                self.anchor_depth = max(0, self.anchor_depth - 1)
            if candidate_id is not None and candidate_id in self.active:
                self.active.remove(candidate_id)
        if tag in self._BLOCK:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1][1]:
            return
        cleaned = data.strip()
        if not cleaned:
            return
        self.body.append(cleaned)
        if self.anchor_depth:
            self.anchor_chars += len(cleaned)
        for candidate_id in self.active:
            self.candidates[candidate_id][1].append(cleaned)

    def _append(self, value: str) -> None:
        if not (self.stack and self.stack[-1][1]):
            self.body.append(value)
            for candidate_id in self.active:
                self.candidates[candidate_id][1].append(value)

    def text(self) -> str:
        raw_body = _SPACE.sub(" ", " ".join(self.body)).strip()
        body = _strip_page_boilerplate(raw_body)
        folded = raw_body.casefold()
        # CBR RSS sometimes links to a section/index page rather than an individual
        # publication. Such pages contain many linked document titles and page chrome;
        # importing the whole listing as the article body pollutes search and AI analysis.
        page_chrome = (
            "страница была полезной?" in folded
            and ("последнее обновление страницы" in folded or "публичное обсуждение" in folded)
        )
        link_heavy_listing = (
            self.anchor_count >= 10
            and self.anchor_chars >= 400
            and len(body) >= 700
            and self.anchor_chars / max(len(body), 1) >= 0.35
        )
        if link_heavy_listing:
            return ""
        eligible = [
            (score, _strip_page_boilerplate(_SPACE.sub(" ", " ".join(parts)).strip()))
            for score, parts in self.candidates
        ]
        eligible = [(score, text) for score, text in eligible if len(text) >= _MIN_ARTICLE_TEXT]
        for score, candidate_text in sorted(
            eligible, key=lambda pair: (pair[0], min(len(pair[1]), _MAX_ARTICLE_TEXT)), reverse=True
        ):
            # A focused article container is trustworthy even when the site's
            # global footer includes page-feedback controls.
            if score >= 90 and len(candidate_text) >= _MIN_ARTICLE_TEXT:
                return candidate_text
        if page_chrome:
            # A section/index page without a focused article is not a news story.
            return ""
        for _score, candidate_text in sorted(
            eligible, key=lambda pair: (pair[0], min(len(pair[1]), _MAX_ARTICLE_TEXT)), reverse=True
        ):
            if len(candidate_text) >= len(body) * 0.25:
                return candidate_text
        return body


def _strip_page_boilerplate(value: str) -> str:
    """Remove recurring CBR page controls and metadata that are not article content."""
    # Some RSS fields contain literal "\n" sequences rather than line breaks.
    text = re.sub(r"\\[nrt]", " ", value)
    patterns = (
        r"(?i)\bстраница была полезной\s*\?",
        r"(?i)\bответственное структурное подразделение\s*:\s*.*$",
        r"(?i)\bпоследнее обновление страницы\s*:?\s*.*$",
        r"(?i)\bверсия для печати\b",
        r"(?i)\bподелиться\b",
    )
    for pattern in patterns:
        text = re.sub(pattern, " ", text)
    return _SPACE.sub(" ", text).strip(" \t\r\n|·—-:")


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    # RSS sometimes escapes markup twice. Decode entities before detecting tags,
    # otherwise literal "<p>" and "<br>" leak into the article text.
    decoded = html.unescape(value)
    if "<" not in decoded:
        return _strip_page_boilerplate(decoded)
    parser = _ArticleTextParser()
    parser.feed(decoded)
    parser.close()
    return parser.text()


def _limit_article_text(value: str) -> str:
    text = _SPACE.sub(" ", value).strip()
    if len(text) <= _MAX_ARTICLE_TEXT:
        return text
    clipped = text[:_MAX_ARTICLE_TEXT].rsplit(" ", 1)[0]
    return clipped or text[:_MAX_ARTICLE_TEXT]


def _fetch_article(url: str, source: NewsSource, timeout_s: float) -> str | None:
    parsed = urlsplit(url)
    source_host = (urlsplit(source.url).hostname or "").casefold()
    article_host = (parsed.hostname or "").casefold()
    root_host = source_host.removeprefix("www.")
    allowed_hosts = {source_host, root_host, f"www.{root_host}"}
    if parsed.scheme != "https" or article_host not in allowed_hosts or parsed.username or parsed.password:
        logger.warning("news-article-extract-skipped source=%s reason=host_not_allowed", source.id)
        return None
    response = _get_official_page(
        url, timeout_s,
        {"User-Agent": "NewBad/0.1 (+official RSS article extraction)", "Accept": "text/html,application/xhtml+xml"},
    )
    response.raise_for_status()
    if len(response.content) > _MAX_ARTICLE_BYTES:
        raise ValueError("страница статьи превышает лимит 2 МиБ")
    content_type = response.headers.get("content-type", "").casefold()
    if content_type and "html" not in content_type and "xhtml" not in content_type:
        return None
    return response.text
