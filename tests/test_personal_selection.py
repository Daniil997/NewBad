from app.agent import generate
from app.corpus.search import NewsItem
from app.llm.router import Route


def _news(news_id="n1"):
    return NewsItem(
        id=news_id, url="https://example.com/news", title="Изменения в логистике",
        published_at="2026-10-20", excerpt="Изменились условия перевозок и поставок.",
        event_type="sanctions_trade", event_title="Логистика", source="Источник",
        status="imported",
    )


def test_select_profile_news_accepts_only_valid_candidate_ids(monkeypatch):
    item = _news()
    route = Route(mode="local_only", provider="local", model="qwen", reason="test")
    monkeypatch.setattr(
        generate, "_ask",
        lambda *args, **kwargs: (
            {"items": [{"id": "n1", "relevant": True, "score": 0.91, "reason": "Связано с логистикой компании"}]},
            route, 12,
        ),
    )

    result = generate.select_profile_news({"industry": "логистика"}, [item])

    assert result == {
        "n1": {
            "relevant": True, "score": 0.91,
            "reasons": ["Связано с логистикой компании"], "method": "LLM (local/qwen)",
        }
    }


def test_select_profile_news_rejects_incomplete_or_unknown_ids(monkeypatch):
    route = Route(mode="local_only", provider="local", model="qwen", reason="test")
    monkeypatch.setattr(
        generate, "_ask",
        lambda *args, **kwargs: (
            {"items": [{"id": "not-a-candidate", "relevant": True, "score": 0.99, "reason": "Подходит"}]},
            route, 12,
        ),
    )

    assert generate.select_profile_news({"industry": "логистика"}, [_news()]) is None


def test_select_profile_news_falls_back_when_model_output_is_unavailable(monkeypatch):
    route = Route(mode="mock", provider="mock", model="mock-model", reason="mock")
    monkeypatch.setattr(generate, "_ask", lambda *args, **kwargs: (None, route, 0))

    assert generate.select_profile_news({"industry": "логистика"}, [_news()]) is None
