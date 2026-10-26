"""Small, same-host website crawler with pinned public-IP connections."""

import http.client
import ipaddress
import socket
import ssl
import time
import heapq
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import SplitResult, quote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "NewBadProfileBot/1.0 (+user-requested profile extraction)"
MAX_PAGES = 10
MAX_DEPTH = 2
MAX_PAGE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 10 * MAX_PAGE_BYTES
MAX_PAGE_TEXT = 12_000
MAX_CRAWL_SECONDS = 35
MAX_REDIRECTS = 3
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
HIDDEN_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "object", "form", "nav", "footer", "header", "aside", "button", "select"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
BLOCK_TAGS = {"p", "div", "section", "article", "main", "li", "br", "h1", "h2", "h3", "h4", "tr", "td", "dt", "dd"}


class SiteFetchError(ValueError):
    """The submitted site could not be fetched under crawler safety rules."""


@dataclass
class SitePage:
    url: str
    title: str
    text: str


@dataclass
class CrawlResult:
    pages: list[SitePage]
    warnings: list[str]
    opened_url: str = ""


@dataclass
class SafeResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str
    truncated: bool = False


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden = 0
        self._in_title = False
        self._text: list[str] = []
        self._title: list[str] = []
        self._stack: list[tuple[str, bool]] = []
        self._descriptions: list[str] = []
        self._seen_links: set[str] = set()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        style = attributes.get("style", "") or ""
        suppressed = (
            tag in HIDDEN_TAGS or "hidden" in attributes
            or (attributes.get("aria-hidden") or "").lower() == "true"
            or "display:none" in style.replace(" ", "").lower()
        )
        if tag not in VOID_TAGS:
            self._stack.append((tag, suppressed))
        if suppressed and tag not in VOID_TAGS:
            self._hidden += 1
        if tag == "title":
            self._in_title = True
        if tag in {"a", "z-link"} and len(self.links) < 1000:
            href = (attributes.get("href") or "").strip()
            if href and href not in self._seen_links:
                self._seen_links.add(href)
                self.links.append(href)
        if tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            content = _clean_text(attributes.get("content") or "")[:1200]
            if name in {"description", "og:description"} and content and len(self._descriptions) < 2:
                self._descriptions.append(content)
        if tag in BLOCK_TAGS and not self._hidden:
            self._text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                self._hidden -= sum(hidden for _, hidden in self._stack[index:])
                del self._stack[index:]
                break
        if tag in BLOCK_TAGS and not self._hidden:
            self._text.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
        if not self._hidden and not self._in_title and data.strip():
            self._text.append(data.strip())

    @property
    def title(self) -> str:
        return _clean_text(" ".join(self._title))[:300]

    @property
    def text(self) -> str:
        return _clean_text(" ".join(dict.fromkeys([*self._descriptions, self.body_text])))[:MAX_PAGE_TEXT]

    @property
    def body_text(self) -> str:
        return _clean_text(" ".join(self._text))[:MAX_PAGE_TEXT]


def crawl_site(start_url: str) -> CrawlResult:
    start = _canonical_url(start_url)
    submitted_host = urlsplit(start).hostname or ""
    deadline = time.monotonic() + MAX_CRAWL_SECONDS
    warnings: list[str] = []
    try:
        landing = _request_following_same_host(
            start, submitted_host, deadline, MAX_PAGE_BYTES, allow_family=True,
        )
    except SiteFetchError as exc:
        return CrawlResult([], [str(exc)])
    host = (urlsplit(landing.url).hostname or submitted_host).lower()
    landing_url = _canonical_url(landing.url)
    opened_url = landing_url if host != submitted_host else ""
    robots, robots_error = _load_robots(f"https://{host}/", host, deadline)
    if robots_error:
        return CrawlResult([], [*warnings, robots_error], opened_url)

    pages: list[SitePage] = []
    total_bytes = 0
    fetched_pages = 0
    queue: list[tuple[int, int, str]] = [(-1, 0, landing_url)]
    queued = {landing_url}
    visited: set[str] = set()
    cached = {landing_url: landing}
    while queue and fetched_pages < MAX_PAGES and time.monotonic() < deadline:
        _, depth, url = heapq.heappop(queue)
        if url in visited:
            continue
        visited.add(url)
        if not robots.can_fetch(USER_AGENT, url):
            warnings.append("Часть страниц закрыта правилами robots.txt.")
            continue
        fetched_pages += 1
        response = cached.pop(url, None)
        if response is None:
            try:
                response = _request_following_same_host(url, host, deadline, MAX_PAGE_BYTES)
            except SiteFetchError as exc:
                warnings.append(str(exc))
                continue
        if response.status < 200 or response.status >= 300:
            warnings.append(f"Часть страниц сайта недоступна (HTTP {response.status}).")
            continue
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            continue
        total_bytes += len(response.body)
        if total_bytes > MAX_TOTAL_BYTES:
            break
        charset = _charset(response.headers.get("content-type", ""))
        try:
            html = response.body.decode(charset, errors="replace")
        except LookupError:
            html = response.body.decode("utf-8", errors="replace")
        parser = _VisibleTextParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception:
            warnings.append("Не удалось разобрать одну из страниц сайта.")
            continue
        page_url = response.url
        if response.truncated:
            warnings.append("Большие страницы прочитаны частично из-за ограничения размера.")
        if parser.text:
            pages.append(SitePage(url=page_url, title=parser.title, text=parser.text))
        if not parser.body_text:
            warnings.append("Часть страниц не содержит доступного текста без JavaScript; использованы только опубликованные описания, если они есть.")
        if depth >= MAX_DEPTH:
            continue
        # Give scarce crawl slots to public company facts: about, products,
        # services and locations before news, banners, pagers and catalog filters.
        discovered = {
            target for href in parser.links
            if (target := _safe_same_host_link(page_url, href, host))
            and target not in visited and target not in queued
        }
        for target in sorted(discovered, key=lambda link: (_profile_link_priority(link), link)):
            if len(queue) + len(visited) >= MAX_PAGES * 8:
                break
            queued.add(target)
            heapq.heappush(queue, (_profile_link_priority(target), depth + 1, target))

    if time.monotonic() >= deadline:
        warnings.append("Истекло время, отведённое на обход сайта.")
    if not pages and not warnings:
        warnings.append("На сайте не найдено доступного текстового содержимого.")
    return CrawlResult(pages=pages, warnings=_dedupe(warnings), opened_url=opened_url)


def _profile_link_priority(url: str) -> int:
    """Prioritize useful public pages without granting more crawl permissions."""
    path = urlsplit(url).path.lower().rstrip("/")
    if any(term in path for term in ("/about", "/company", "/o-kompanii", "/o-nas", "/about-us", "/о-компании")):
        return 0
    if any(term in path for term in ("/services", "/products", "/catalog", "/production", "/uslugi", "/produkci", "/услуги", "/продукц")):
        return 1
    if any(term in path for term in ("/contacts", "/contact", "/offices", "/branches", "/контакты")):
        return 2
    if any(term in path for term in ("/partners", "/projects", "/clients", "/markets", "/география")):
        return 3
    if any(term in path for term in ("/news", "/blog", "/press", "/article", "/events", "/legal", "/privacy", "/login", "/basket")):
        return 9
    return 5


def _load_robots(start_url: str, host: str, deadline: float) -> tuple[RobotFileParser, str]:
    parsed = urlsplit(start_url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    parser = RobotFileParser()
    try:
        response = _request_following_same_host(robots_url, host, deadline, 64 * 1024)
    except SiteFetchError:
        return parser, "Не удалось безопасно проверить robots.txt; обход сайта остановлен."
    if response.status in {401, 403} or response.status >= 500 or response.truncated:
        return parser, "Сайт не разрешил проверить robots.txt; обход остановлен."
    if 200 <= response.status < 300:
        parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
    else:
        parser.parse([])
    return parser, ""


def _request_following_same_host(
    url: str,
    expected_host: str,
    deadline: float,
    max_bytes: int,
    allow_family: bool = False,
) -> SafeResponse:
    current = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        parsed, addresses = _validated_target(current, expected_host, allow_family=allow_family)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SiteFetchError("Истекло время ожидания ответа сайта.")
        for address in addresses[:3]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SiteFetchError("Истекло время ожидания ответа сайта.")
            try:
                response = _request_pinned(parsed, address, min(5.0, remaining), max_bytes, deadline)
                break
            except SiteFetchError:
                if address == addresses[:3][-1]:
                    raise
        if response.status not in {301, 302, 303, 307, 308}:
            return response
        if redirect_count >= MAX_REDIRECTS:
            raise SiteFetchError("Сайт превысил лимит перенаправлений.")
        location = response.headers.get("location", "")
        if not location:
            raise SiteFetchError("Сайт вернул перенаправление без адреса.")
        redirected = _canonical_url(urljoin(current, location))
        redirected_parts = urlsplit(redirected)
        if redirected_parts.scheme != parsed.scheme and parsed.scheme == "https":
            raise SiteFetchError("Отклонено перенаправление с HTTPS на HTTP.")
        current = redirected
    raise SiteFetchError("Не удалось получить страницу сайта.")


def _host_in_submitted_family(hostname: str, submitted_host: str) -> bool:
    """Allow the submitted host, its www form, and subdomains of a company apex."""
    candidate = hostname.lower().rstrip(".")
    submitted = submitted_host.lower().rstrip(".")
    if candidate == submitted or candidate == f"www.{submitted}" or submitted == f"www.{candidate}":
        return True
    apex = submitted[4:] if submitted.startswith("www.") else submitted
    if apex.count(".") != 1:
        return False
    return candidate == apex or candidate.endswith(f".{apex}")


def _validated_target(url: str, expected_host: str, allow_family: bool = False) -> tuple[SplitResult, list[str]]:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise SiteFetchError("Некорректный адрес сайта.") from exc
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        raise SiteFetchError("Разрешены только публичные сайты с HTTP или HTTPS.")
    try:
        hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise SiteFetchError("Некорректное имя сайта.") from exc
    if allow_family:
        allowed = _host_in_submitted_family(hostname, expected_host)
    else:
        allowed = hostname == expected_host
    if not allowed:
        raise SiteFetchError("Перенаправление или ссылка за пределы указанного сайта запрещены.")
    if port not in (None, 80, 443) or (parsed.scheme == "https" and port == 80) or (parsed.scheme == "http" and port == 443):
        raise SiteFetchError("Нестандартный порт сайта запрещён.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise SiteFetchError("В адресе сайта должно быть доменное имя, а не IP-адрес.")
    port = port or (443 if parsed.scheme == "https" else 80)
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SiteFetchError("Не удалось найти публичный адрес сайта.") from exc
    addresses = sorted({record[4][0] for record in records})
    if not addresses:
        raise SiteFetchError("У сайта нет доступных сетевых адресов.")
    try:
        parsed_addresses = [ipaddress.ip_address(item.split("%", 1)[0]) for item in addresses]
    except ValueError as exc:
        raise SiteFetchError("DNS сайта вернул некорректный адрес.") from exc
    if any(not address.is_global for address in parsed_addresses):
        raise SiteFetchError("Сайт указывает на непубличный сетевой адрес; запрос отклонён.")
    return parsed, addresses


def _request_pinned(parsed: SplitResult, address: str, timeout: float, max_bytes: int, deadline: float) -> SafeResponse:
    hostname = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection = http.client.HTTPConnection(hostname, port, timeout=timeout)
    try:
        sock = socket.create_connection((address, port), timeout=timeout)
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=hostname)
        connection.sock = sock
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if parsed.query:
            safe_query = "/?:@!$&'()*+,;=-._~%"
            path = "{}?{}".format(path, quote(parsed.query, safe=safe_query))
        connection.request(
            "GET",
            path,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
            raise SiteFetchError("Сжатые ответы сайта не поддерживаются.")
        try:
            announced_size = int(headers.get("content-length", "0"))
        except ValueError:
            announced_size = 0
        body = bytearray()
        while len(body) < max_bytes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SiteFetchError("Истекло время ожидания ответа сайта.")
            if connection.sock:
                connection.sock.settimeout(min(2.0, remaining))
            chunk = response.read(min(16 * 1024, max_bytes - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        truncated = announced_size > len(body)
        if not truncated and len(body) == max_bytes:
            truncated = bool(response.read(1))
        final_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
        return SafeResponse(
            status=response.status, headers=headers, body=bytes(body), url=final_url, truncated=truncated,
        )
    except SiteFetchError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError) as exc:
        raise SiteFetchError("Не удалось загрузить страницу сайта.") from exc
    finally:
        connection.close()


def _canonical_url(url: str) -> str:
    if any(ord(char) < 32 or char == "\\" for char in url):
        raise SiteFetchError("Адрес сайта содержит недопустимые символы.")
    try:
        parsed = urlsplit(url.strip())
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise SiteFetchError("Некорректный адрес сайта.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        raise SiteFetchError("Укажите адрес сайта.")
    try:
        host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise SiteFetchError("Некорректное имя сайта.") from exc
    netloc = host
    if port and port != (443 if parsed.scheme.lower() == "https" else 80):
        netloc = f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _safe_same_host_link(page_url: str, href: str, expected_host: str) -> str | None:
    try:
        target = _canonical_url(urljoin(page_url, href))
        parsed = urlsplit(target)
        hostname = parsed.hostname or ""
        hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (SiteFetchError, UnicodeError):
        return None
    if hostname != expected_host or parsed.scheme not in {"http", "https"}:
        return None
    source = urlsplit(page_url)
    if source.scheme == "https" and parsed.scheme == "http":
        return None
    if parsed.port not in (None, 80, 443):
        return None
    if parsed.path.lower().endswith((".pdf", ".jpg", ".png", ".zip", ".xlsx", ".docx", ".js", ".css")):
        return None
    return target


def _charset(content_type: str) -> str:
    for token in content_type.split(";")[1:]:
        key, separator, value = token.partition("=")
        if separator and key.strip().lower() == "charset":
            return value.strip(" \"'")[:40] or "utf-8"
    return "utf-8"


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
