from urllib.robotparser import RobotFileParser

import pytest

from app.profile import site_crawler
from app.profile.site_crawler import SafeResponse, SiteFetchError, _canonical_url, _validated_target


def test_rejects_private_and_ip_literal_targets(monkeypatch):
    monkeypatch.setattr(site_crawler.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("127.0.0.1", 80)),
    ])
    with pytest.raises(SiteFetchError, match="непубличный"):
        _validated_target("http://public.example/", "public.example")
    with pytest.raises(SiteFetchError, match="IP-адрес"):
        _validated_target("http://127.0.0.1/", "127.0.0.1")


def test_rejects_malformed_urls_and_preserves_safe_query():
    with pytest.raises(SiteFetchError):
        _canonical_url("http://example.org:bad/")
    assert _canonical_url("https://example.org/catalog?page=2#top") == "https://example.org/catalog?page=2"


def test_crawl_obeys_robots_same_host_and_skips_hidden_text(monkeypatch):
    monkeypatch.setattr(site_crawler.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("93.184.216.34", 443)),
    ])
    robots = RobotFileParser()
    robots.parse(["User-agent: *", "Disallow: /private"])
    monkeypatch.setattr(site_crawler, "_load_robots", lambda *args: (robots, ""))

    def fetch(url, host, deadline, max_bytes, allow_family=False):
        body = (
            b'<html><title>Company</title><script>secret</script><p>Public business text</p>'
            b'<a href="/about?view=full">About</a><a href="/private">Private</a>'
            b'<a href="https://other.example/">External</a></html>'
            if url.endswith("/")
            else b"<html><p>About our company</p></html>"
        )
        return SafeResponse(200, {"content-type": "text/html; charset=utf-8"}, body, url)

    monkeypatch.setattr(site_crawler, "_request_following_same_host", fetch)
    result = site_crawler.crawl_site("https://example.org/")
    assert len(result.pages) == 2
    assert result.pages[0].title == "Company"
    assert "secret" not in result.pages[0].text
    assert any("robots.txt" in warning for warning in result.warnings)
    assert all("other.example" not in page.url for page in result.pages)


def test_redirect_cannot_leave_site_or_downgrade_https(monkeypatch):
    monkeypatch.setattr(site_crawler.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("93.184.216.34", 443)),
    ])
    monkeypatch.setattr(
        site_crawler, "_request_pinned",
        lambda parsed, address, timeout, max_bytes, deadline: SafeResponse(
            302, {"location": "https://attacker.example/"}, b"", "https://example.org/"
        ),
    )
    with pytest.raises(SiteFetchError, match="за пределы"):
        site_crawler._request_following_same_host("https://example.org/", "example.org", 999999999, 1000)


def test_company_apex_follows_its_own_regional_host_and_keeps_a_large_page(monkeypatch):
    monkeypatch.setattr(site_crawler.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("93.184.216.34", 443)),
    ])

    def pinned(parsed, address, timeout, max_bytes, deadline):
        host = parsed.hostname
        path = parsed.path or "/"
        if host == "mts.ru":
            return SafeResponse(302, {"location": "https://region.mts.ru/about"}, b"", "https://mts.ru/")
        if path == "/robots.txt":
            return SafeResponse(
                200, {"content-type": "text/plain"}, b"User-agent: *\nAllow: /\n", "https://region.mts.ru/robots.txt",
            )
        if path == "/about":
            body = b"<html><title>MTS</title><p>Mobile operator and digital services for companies.</p></html>"
            return SafeResponse(
                200,
                {"content-type": "text/html; charset=utf-8", "content-length": str(max_bytes + 50)},
                body,
                "https://region.mts.ru/about",
                truncated=True,
            )
        raise AssertionError(f"{host}{path}")

    monkeypatch.setattr(site_crawler, "_request_pinned", pinned)
    result = site_crawler.crawl_site("https://mts.ru")
    assert [page.url for page in result.pages] == ["https://region.mts.ru/about"]
    assert "digital services" in result.pages[0].text
    assert result.opened_url == "https://region.mts.ru/about"
    assert result.warnings == []


def test_named_host_does_not_follow_a_sibling_or_foreign_site(monkeypatch):
    monkeypatch.setattr(site_crawler.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("93.184.216.34", 443)),
    ])
    monkeypatch.setattr(
        site_crawler, "_request_pinned",
        lambda parsed, address, timeout, max_bytes, deadline: SafeResponse(
            302, {"location": "https://other.mts.ru/"}, b"", "https://region.mts.ru/"
        ),
    )
    sibling = site_crawler.crawl_site("https://region.mts.ru/")
    assert sibling.pages == []
    assert any("за пределы" in warning for warning in sibling.warnings)
    monkeypatch.setattr(
        site_crawler, "_request_pinned",
        lambda parsed, address, timeout, max_bytes, deadline: SafeResponse(
            302, {"location": "https://attacker.example/"}, b"", "https://mts.ru/"
        ),
    )
    foreign = site_crawler.crawl_site("https://mts.ru/")
    assert foreign.pages == []
    assert any("за пределы" in warning for warning in foreign.warnings)

