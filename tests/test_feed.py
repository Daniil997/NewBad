from fastapi.testclient import TestClient

from app.db import connect
from app.main import app
from app.scoring.feed import build_feed


def _rate(client_id: str):
    feed = build_feed(client_id)
    assert feed is not None
    for item in feed.top + feed.less_relevant:
        if item.news_id == "cbr-rate-14":
            return item
    raise AssertionError("новость о ставке не попала в ленту")


def test_rate_is_higher_for_borrower_than_for_peer():
    borrower = _rate("U-BORROWER")
    peer = _rate("U-PEER")
    saver = _rate("U-SAVER")

    assert borrower.score > peer.score
    assert saver.score > peer.score
    assert "rate_borrower" in borrower.matched_features
    assert "rate_saver" not in borrower.matched_features
    assert "rate_saver" in saver.matched_features
    assert "rate_borrower" not in saver.matched_features
    assert any(item.news_id == "cbr-rate-14" for item in build_feed("U-BORROWER").top)


def test_feed_api_uses_cache():
    with TestClient(app) as client:
        first = client.get("/api/clients/U-SAVER/feed")
        second = client.get("/api/clients/U-SAVER/feed")
        missing = client.get("/api/clients/missing/feed")

    assert first.status_code == 200
    assert first.json() == second.json()
    assert missing.status_code == 404
    with connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM feed_cache WHERE client_id = 'U-SAVER' AND corpus_version = ?",
            (first.json()["corpus_version"],),
        ).fetchone()["n"]
    assert count == 1
