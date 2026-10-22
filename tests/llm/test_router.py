import json

from fastapi.testclient import TestClient

from app.db import connect
from app.llm.client import Completion, LLMError
from app.llm.router import choose_tools, complete_tier, resolve_route
from app.main import app

CLOUD_LIGHT = "google/gemma-4-26b-a4b-it"
CLOUD_HEAVY = "qwen/qwen3.6-35b-a3b"
LOCAL_LIGHT = "gemma-4-26b-a4b"


def _reasons() -> list[str]:
    with connect() as connection:
        rows = connection.execute("SELECT switch_reason, model, provider, mode FROM llm_calls ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def test_same_conditions_select_the_same_model(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "local_dual")
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    assert resolve_route("light") == resolve_route("light")
    assert resolve_route("heavy") == resolve_route("heavy")
    assert resolve_route("light").model == LOCAL_LIGHT
    assert resolve_route("heavy").model == "qwen3.6-35b-a3b"
    assert resolve_route("light").provider == "local"


def test_cloud_only_is_blocked_without_permission_or_key(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "cloud_only")
    monkeypatch.delenv("CASE_LLM_API_KEY", raising=False)

    def explode(*_args, **_kwargs):
        raise AssertionError("сеть не должна вызываться")

    monkeypatch.setattr("app.llm.router.complete", explode)
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    blocked = resolve_route("light")
    assert blocked.provider == "mock"
    assert blocked.reason == "облако выключено, ответ собирает заглушка"
    outcome = complete_tier([{"role": "user", "content": "ping"}], tier="light", max_tokens=8)
    assert outcome.text is None
    assert outcome.route.reason == blocked.reason

    monkeypatch.setenv("ALLOW_CLOUD", "true")
    monkeypatch.setenv("CASE_LLM_API_KEY", " ")
    missing = resolve_route("light")
    assert missing.provider == "mock"
    assert missing.reason == "не задан ключ облачного сервиса"
    assert complete_tier([{"role": "user", "content": "ping"}], tier="light", max_tokens=8).text is None
    blob = json.dumps(_reasons(), ensure_ascii=False)
    assert "test-key" not in blob
    assert "Bearer" not in blob


def test_cloud_only_selects_the_configured_model(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "cloud_only")
    monkeypatch.setenv("ALLOW_CLOUD", "true")
    monkeypatch.setenv("CASE_LLM_API_KEY", "test-key")
    first = resolve_route("light")
    second = resolve_route("heavy")
    assert first == resolve_route("light")
    assert first.provider == "cloud"
    assert first.model == CLOUD_LIGHT
    assert second.model == CLOUD_HEAVY
    assert first.reason == "облачный маршрут из конфигурации"
    seen: list[tuple[str, str]] = []

    def fake(_messages, *, model, mode, max_tokens, provider=None):
        del mode, max_tokens
        seen.append((model, provider or ""))
        return Completion('{"tools":["search_news"]}', 3, model, provider or "cloud", "cloud_only")

    monkeypatch.setattr("app.llm.router.complete", fake)
    choice = choose_tools(["search_news", "search_web"])
    assert seen == [(CLOUD_LIGHT, "cloud")]
    assert choice.model == CLOUD_LIGHT
    assert choice.provider == "cloud"
    assert choice.mode == "cloud_only"
    assert choice.tools == ["search_news"]


def test_hybrid_falls_back_to_local_after_one_cloud_attempt(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "hybrid")
    monkeypatch.setenv("ALLOW_CLOUD", "true")
    monkeypatch.setenv("CASE_LLM_API_KEY", "test-key")
    seen: list[str] = []

    def fake(_messages, *, model, mode, max_tokens, provider=None):
        del mode, max_tokens
        seen.append(f"{provider}:{model}")
        if provider == "cloud":
            raise LLMError("облачный сервис отклонил доступ")
        return Completion("локальный ответ", 2, model, "local", "hybrid")

    monkeypatch.setattr("app.llm.router.complete", fake)
    monkeypatch.setattr(
        "app.llm.router._entries",
        lambda _mode, _tier: [
            {"provider": "cloud", "model": CLOUD_LIGHT},
            {"provider": "cloud", "model": CLOUD_HEAVY},
            {"provider": "local", "model": LOCAL_LIGHT},
        ],
    )
    outcome = complete_tier([{"role": "user", "content": "план"}], tier="heavy", max_tokens=20)
    assert seen == [f"cloud:{CLOUD_LIGHT}", f"local:{LOCAL_LIGHT}"]
    assert outcome.route.provider == "local"
    assert outcome.route.model == LOCAL_LIGHT
    assert outcome.text == "локальный ответ"
    reasons = [row["switch_reason"] for row in _reasons()]
    assert any("переход на локальную модель" in reason for reason in reasons)
    assert "test-key" not in json.dumps(reasons, ensure_ascii=False)


def test_hybrid_skips_cloud_when_it_is_switched_off(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "hybrid")
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    seen: list[str] = []

    def fake(_messages, *, model, mode, max_tokens, provider=None):
        del mode, max_tokens
        seen.append(provider or "")
        return Completion("локальный ответ", 1, model, "local", "hybrid")

    monkeypatch.setattr("app.llm.router.complete", fake)
    outcome = complete_tier([{"role": "user", "content": "план"}], tier="heavy", max_tokens=12)
    assert seen == ["local"]
    assert outcome.route.model == LOCAL_LIGHT
    assert _reasons()[0]["switch_reason"] == "облако выключено, ответ собирает заглушка"


def test_replay_reads_the_cache_and_does_not_call_the_network(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "local_only")
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    messages = [{"role": "user", "content": "секрет компании не для кэша"}]

    def fake(_messages, *, model, mode, max_tokens, provider=None):
        del mode, max_tokens, provider
        return Completion("готовый ответ", 4, model, "local", "local_only")

    monkeypatch.setattr("app.llm.router.complete", fake)
    saved = complete_tier(messages, tier="light", max_tokens=16)
    assert saved.text == "готовый ответ"

    def explode(*_args, **_kwargs):
        raise AssertionError("replay сходил в сеть")

    monkeypatch.setattr("app.llm.router.complete", explode)
    monkeypatch.setattr("app.llm.client.post_chat", explode)
    monkeypatch.setenv("LLM_MODE", "replay")
    cached = complete_tier(messages, tier="light", max_tokens=16)
    assert cached.text == "готовый ответ"
    assert cached.route.mode == "replay"
    assert cached.route.reason == "replay: ответ взят из кэша"
    with connect() as connection:
        row = dict(connection.execute("SELECT * FROM llm_replay").fetchone())
    assert "секрет компании" not in json.dumps(row, ensure_ascii=False)
    assert row["response"] == "готовый ответ"

    missed = complete_tier([{"role": "user", "content": "другой запрос"}], tier="light", max_tokens=8)
    assert missed.text is None
    assert missed.route.reason == "replay: записи в кэше нет"


def test_settings_show_cloud_route_without_calling_the_model(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "cloud_only")
    monkeypatch.setenv("ALLOW_CLOUD", "true")
    monkeypatch.setenv("CASE_LLM_API_KEY", "test-key")

    def explode(*_args, **_kwargs):
        raise AssertionError("настройки не должны вызывать модель")

    monkeypatch.setattr("app.llm.router.complete", explode)
    with TestClient(app) as client:
        response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "cloud_only"
    assert body["active_generation"] == "cloud_only"
    assert body["model"] == CLOUD_LIGHT
    assert body["allow_cloud"] is True
    assert body["local_server"] == "not_used"
    assert "test-key" not in response.text
