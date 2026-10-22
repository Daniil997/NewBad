import json

import httpx
import pytest

from app.db import connect
from app.llm.client import LLMError, complete, parse_json, post_chat

CLOUD_URL = "https://changellenge.up.railway.app/v1"
MODEL = "google/gemma-4-26b-a4b-it"


def _ok(url, payload, timeout, headers=None, transport=None):
    del timeout, transport
    _ok.seen = {"url": url, "payload": payload, "headers": headers}
    return {"choices": [{"message": {"content": "готово"}}], "usage": {"total_tokens": 4}}


_ok.seen = {}


def _cloud(monkeypatch, tmp_path, *, allow: str = "true", mode: str = "cloud_only", key: str = "test-key"):
    _ok.seen = {}
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_CLOUD", allow)
    monkeypatch.setenv("LLM_MODE", mode)
    monkeypatch.setenv("CASE_LLM_BASE_URL", CLOUD_URL)
    if key:
        monkeypatch.setenv("CASE_LLM_API_KEY", key)
    else:
        monkeypatch.delenv("CASE_LLM_API_KEY", raising=False)
    monkeypatch.setattr("app.llm.client.post_chat", _ok)
    monkeypatch.setattr("app.llm.client.request_options", lambda: {"num_ctx": 4096, "num_gpu": 99})


def test_local_call_sends_options_once_and_no_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("LLM_TIMEOUT_S", "12")
    calls = {"n": 0}

    def options():
        calls["n"] += 1
        return {"num_ctx": 4096}

    monkeypatch.setattr("app.llm.client.request_options", options)
    monkeypatch.setattr("app.llm.client.post_chat", _ok)
    result = complete(
        [{"role": "user", "content": "локальный вопрос"}],
        model="gemma-4-26b-a4b",
        mode="local_only",
        max_tokens=20,
    )
    assert result.provider == "local"
    assert result.text == "готово"
    assert calls["n"] == 1
    assert _ok.seen["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert _ok.seen["headers"] is None
    assert _ok.seen["payload"]["options"] == {"num_ctx": 4096}
    assert _ok.seen["payload"]["max_tokens"] == 20
    assert list(_ok.seen["payload"]) == ["model", "messages", "temperature", "max_tokens", "options"]


def test_json_parser_accepts_a_single_object_inside_model_wrappers():
    assert parse_json('Ответ:\n```json\n{"answer":"готово"}\n```\n') == {"answer": "готово"}
    assert parse_json('Пояснение {"answer":"готово"} конец') == {"answer": "готово"}
    with pytest.raises(LLMError, match="не JSON"):
        parse_json("только текст без объекта")


def test_cloud_call_uses_env_url_and_bearer_header(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path)
    monkeypatch.setenv("CASE_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    result = complete(
        [{"role": "user", "content": "секрет компании не для журнала"}],
        model=MODEL,
        mode="cloud_only",
        max_tokens=16,
    )
    assert result.provider == "cloud"
    assert result.model == MODEL
    assert result.tokens == 4
    assert _ok.seen["url"] == "http://127.0.0.1:9/v1/chat/completions"
    assert "test-key" not in _ok.seen["url"]
    assert "options" not in _ok.seen["payload"]
    assert _ok.seen["payload"]["response_format"] == {"type": "json_object"}
    assert "test-key" not in json.dumps(_ok.seen["payload"])
    assert _ok.seen["headers"] == {"Authorization": "Bearer test-key"}
    with connect() as connection:
        row = connection.execute("SELECT model, provider, mode, tokens, switch_reason FROM llm_calls").fetchone()
    blob = json.dumps(dict(row), ensure_ascii=False)
    assert row["switch_reason"] == "облачный вызов"
    assert "test-key" not in blob
    assert "секрет компании" not in blob
    assert "Authorization" not in blob


def test_cloud_url_comes_from_yaml_when_env_is_empty(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path)
    monkeypatch.setenv("CASE_LLM_BASE_URL", "")
    complete([{"role": "user", "content": "ping"}], model=MODEL, mode="cloud_only", max_tokens=8)
    assert _ok.seen["url"] == f"{CLOUD_URL}/chat/completions"


def test_cloud_is_blocked_without_allow_flag(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path, allow="false")
    with pytest.raises(LLMError, match="облако выключено"):
        complete([{"role": "user", "content": "ping"}], model=MODEL, mode="cloud_only", max_tokens=8)
    assert _ok.seen == {}


def test_cloud_provider_is_refused_in_local_mode(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path, mode="local_only")
    with pytest.raises(LLMError, match="только в облачном режиме"):
        complete(
            [{"role": "user", "content": "ping"}],
            model=MODEL,
            mode="local_only",
            max_tokens=8,
            provider="cloud",
        )
    assert _ok.seen == {}


def test_unknown_model_is_rejected_before_the_request(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path)
    with pytest.raises(LLMError, match="не разрешена"):
        complete([{"role": "user", "content": "ping"}], model="gpt-4", mode="cloud_only", max_tokens=8)
    assert _ok.seen == {}


def test_local_alias_is_sent_as_the_api_id(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path)
    complete(
        [{"role": "user", "content": "ping"}],
        model="gemma-4-26b-a4b",
        mode="cloud_only",
        max_tokens=8,
    )
    assert _ok.seen["payload"]["model"] == "google/gemma-4-26b-a4b-it"


def test_token_cap_cuts_an_oversized_request(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path)
    complete([{"role": "user", "content": "ping"}], model=MODEL, mode="cloud_only", max_tokens=5000)
    assert _ok.seen["payload"]["max_tokens"] == 400


def test_cloud_requires_a_key_and_does_not_echo_it(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path, key="")
    with pytest.raises(LLMError, match="не задан ключ") as caught:
        complete([{"role": "user", "content": "ping"}], model=MODEL, mode="cloud_only", max_tokens=8)
    assert "Bearer" not in str(caught.value)
    assert _ok.seen == {}


def test_status_timeout_and_bad_payload_stay_safe(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path)

    def status(url, payload, timeout, headers=None, transport=None):
        del url, payload, timeout, headers, transport
        request = httpx.Request("POST", CLOUD_URL)
        response = httpx.Response(401, json={"error": "test-key", "hint": "секрет компании"}, request=request)
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr("app.llm.client.post_chat", status)
    with pytest.raises(LLMError, match="отклонил доступ") as denied:
        complete([{"role": "user", "content": "секрет компании"}], model=MODEL, mode="cloud_only", max_tokens=8)
    assert denied.value.__cause__ is None
    assert "test-key" not in str(denied.value)
    assert "секрет компании" not in str(denied.value)

    def limited(url, payload, timeout, headers=None, transport=None):
        del url, payload, timeout, headers, transport
        request = httpx.Request("POST", CLOUD_URL)
        response = httpx.Response(429, json={"error": "budget test-key"}, request=request)
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr("app.llm.client.post_chat", limited)
    with pytest.raises(LLMError, match="лимит запросов или бюджет"):
        complete([{"role": "user", "content": "ping"}], model=MODEL, mode="cloud_only", max_tokens=8)

    def slow(url, payload, timeout, headers=None, transport=None):
        del url, payload, timeout, headers, transport
        raise httpx.TimeoutException("timeout at test-key")

    monkeypatch.setattr("app.llm.client.post_chat", slow)
    with pytest.raises(LLMError, match="время ожидания") as waited:
        complete([{"role": "user", "content": "ping"}], model=MODEL, mode="cloud_only", max_tokens=8)
    assert "test-key" not in str(waited.value)

    def down(url, payload, timeout, headers=None, transport=None):
        del url, payload, timeout, headers, transport
        raise httpx.ConnectError("network")

    monkeypatch.setattr("app.llm.client.post_chat", down)
    with pytest.raises(LLMError, match="недоступен"):
        complete([{"role": "user", "content": "ping"}], model=MODEL, mode="cloud_only", max_tokens=8)

    def broken(url, payload, timeout, headers=None, transport=None):
        del url, payload, timeout, headers, transport
        return {"choices": []}

    monkeypatch.setattr("app.llm.client.post_chat", broken)
    with pytest.raises(LLMError, match="некорректный ответ"):
        complete([{"role": "user", "content": "ping"}], model=MODEL, mode="cloud_only", max_tokens=8)


def test_replay_and_mock_do_not_call_the_network(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path)
    with pytest.raises(LLMError, match="replay не ходит в сеть"):
        complete([{"role": "user", "content": "ping"}], model=MODEL, mode="replay", max_tokens=8, provider="cloud")
    with pytest.raises(LLMError, match="заглушка не ходит в сеть"):
        complete([{"role": "user", "content": "ping"}], model="mock-model", mode="mock", max_tokens=8)
    assert _ok.seen == {}


def test_transport_keeps_the_key_out_of_the_url_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.content.decode()
        assert "test-key" not in str(request.url)
        assert "test-key" not in raw
        assert "options" not in json.loads(raw)
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 1}})

    body = post_chat(
        f"{CLOUD_URL}/chat/completions",
        {"model": MODEL, "messages": [{"role": "user", "content": "ping"}]},
        5,
        {"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(handler),
    )
    assert body["choices"][0]["message"]["content"] == "ok"
