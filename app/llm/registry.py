ALLOWED_MODELS = {
    "mock-model": "заглушка",
    "gemma-4-26b-a4b": "Gemma 4 26B A4B, QAT Q4_0",
    "qwen3.6-35b-a3b": "Qwen3.6 35B A3B",
}


def require_allowed(model: str) -> str:
    if model not in ALLOWED_MODELS:
        raise ValueError(f"Модель не из списка кейса: {model}")
    return model
