"""Куда класть локальную модель: целиком на видеокарту или с хвостом в оперативной памяти."""

import os
import subprocess
import time

# Замер llama.cpp на Gemma 4 26B A4B Q4_0, контекст 4096:
# веса 13755 + кэш 380 + расчёт 148 + запас, который просит загрузчик, 2170.
FULL_GPU_FREE_MIB = 16453
NUM_CTX = 4096
_CACHE_S = 30.0
_cache: tuple[float, int | None] | None = None


def free_vram_mib() -> int | None:
    """Свободная память дискретной NVIDIA в МиБ. None, если карту не прочитали."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_S:
        return _cache[1]
    free = _query_nvidia()
    _cache = (now, free)
    return free


def clear_cache() -> None:
    global _cache
    _cache = None


def decide() -> tuple[str, int | None]:
    """Возвращает ('gpu'|'ram', свободно МиБ). Режим задаёт LLM_PLACEMENT, по умолчанию auto."""
    policy = os.environ.get("LLM_PLACEMENT", "auto").strip().lower()
    free = free_vram_mib()
    if policy == "gpu":
        return "gpu", free
    if policy == "ram":
        return "ram", free
    if free is not None and free >= FULL_GPU_FREE_MIB:
        return "gpu", free
    return "ram", free


def request_options() -> dict[str, int] | None:
    """Параметры для Ollama. На llama-server их не отправляем: у него другой запуск."""
    base = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    if "11434" not in base:
        return None
    place, _free = decide()
    options = {"num_ctx": NUM_CTX}
    if place == "gpu":
        options["num_gpu"] = 99
    return options


def placement_label(place: str, free: int | None) -> str:
    if place == "gpu":
        where = "только видеокарта"
    else:
        where = "видеокарта и оперативная память"
    if free is None:
        return where
    return f"{where}, свободно {free} МиБ"


def _query_nvidia() -> int | None:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    line = completed.stdout.strip().splitlines()
    if not line or not line[0].strip().isdigit():
        return None
    return int(line[0].strip())
