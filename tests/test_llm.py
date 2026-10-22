import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm.placement import request_options
from app.llm.registry import require_allowed
from app.main import app


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        require_allowed("gpt-4")
    assert require_allowed("gemma-4-26b-a4b") == "gemma-4-26b-a4b"


def test_local_mode_extracts_and_drafts_pasted_news(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "local_only")
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seen: list[str] = []

    def fake_post(_url: str, payload: dict, timeout: float, headers: dict | None = None) -> dict:
        del timeout, headers
        system = payload["messages"][0]["content"]
        user = json.loads(payload["messages"][1]["content"])
        seen.append(system)
        if "tools" in system:
            content = '{"tools":["get_profile","search_news","search_bank_analytics","get_counterparties"]}'
        elif "факт" in system:
            evidence_id = user["evidence"][0]["evidence_id"]
            content = json.dumps(
                {
                    "text": "Совет директоров сохранил ключевую ставку на уровне 14%.",
                    "layer": "confirmed_fact",
                    "evidence_ids": [evidence_id],
                },
                ensure_ascii=False,
            )
        else:
            news_id = user["evidence"][0]["evidence_id"]
            bank = next((item for item in user["evidence"] if item["layer"] == "bank_position"), None)
            content = json.dumps(
                {
                    "fact": {
                        "text": "Совет директоров сохранил ключевую ставку на уровне 14%.",
                        "layer": "confirmed_fact",
                        "evidence_ids": [news_id],
                    },
                    "bank": None
                    if bank is None
                    else {"text": bank["text"], "layer": "bank_position", "evidence_ids": [bank["evidence_id"]]},
                    "inference": {
                        "layer": "model_inference",
                        "evidence_ids": [news_id] if bank is None else [news_id, bank["evidence_id"]],
                        "why": "Компания пользуется кредитом, поэтому ставка держит стоимость заёмных денег.",
                        "risk": "Платежи по кредиту не дешевеют.",
                        "opportunity": "Можно пересчитать график кредита.",
                        "next_step": "Сверить платежи по кредиту со ставкой 14%.",
                        "question": "Какая доля кредита зависит от ставки?",
                    },
                },
                ensure_ascii=False,
            )
        return {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 11}}

    monkeypatch.setattr("app.llm.client.post_chat", fake_post)
    with TestClient(app) as client:
        created = client.post(
            "/api/clients/U-BORROWER/news",
            json={
                "title": "Ключевая ставка сохранена",
                "text": "Совет директоров сохранил ключевую ставку на уровне 14% годовых.",
            },
        )
    assert created.status_code == 200
    card = created.json()
    fact = card["what_happened"]["claims"][0]["text"]
    why = " ".join(item["text"] for item in card["why_for_company"]["claims"])
    assert fact == "Совет директоров сохранил ключевую ставку на уровне 14%."
    assert why == "Компания пользуется кредитом, поэтому ставка держит стоимость заёмных денег."
    assert card["llm_trace"]["mode"] == "local_only"
    assert card["llm_trace"]["model"] == "gemma-4-26b-a4b"
    assert card["llm_trace"]["origin"] == "model"
    assert card["llm_trace"]["origin"] == "model"
    assert card["llm_trace"]["origin"] == "model"
    assert card["bank_view"]["quote"]
    assert any("факт" in item for item in seen)
    assert any("tools" in item for item in seen)


def test_local_mode_falls_back_when_server_is_down(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "local_only")
    monkeypatch.setenv("ALLOW_CLOUD", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def down(_url: str, _payload: dict, timeout: float, headers: dict | None = None) -> dict:
        del timeout, headers
        raise httpx.ConnectError("нет сервера")

    monkeypatch.setattr("app.llm.client.post_chat", down)
    with TestClient(app) as client:
        response = client.post("/api/clients/U-BORROWER/events/cbr-rate-14/card")
    assert response.status_code == 200
    card = response.json()
    assert card["llm_trace"]["mode"] == "mock"
    assert card["llm_trace"]["origin"] == "template"
    assert card["llm_trace"]["origin"] == "template"
    assert "стоимость заёмных денег" in " ".join(item["text"] for item in card["why_for_company"]["claims"])


def test_auto_keeps_tail_in_ram_when_vram_is_short(monkeypatch):
    monkeypatch.setenv("LLM_PLACEMENT", "auto")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr("app.llm.placement.free_vram_mib", lambda: 12000)
    assert request_options() == {"num_ctx": 4096}


def test_auto_uses_gpu_when_vram_fits(monkeypatch):
    monkeypatch.setenv("LLM_PLACEMENT", "auto")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr("app.llm.placement.free_vram_mib", lambda: 17000)
    assert request_options() == {"num_ctx": 4096, "num_gpu": 99}


def test_llama_server_does_not_receive_ollama_options(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setattr("app.llm.placement.free_vram_mib", lambda: 12000)
    assert request_options() is None
