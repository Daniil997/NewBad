
import pytest

from app.llm.placement import request_options
from app.llm.registry import require_allowed


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        require_allowed("gpt-4")
    assert require_allowed("gemma-4-26b-a4b") == "gemma-4-26b-a4b"


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
