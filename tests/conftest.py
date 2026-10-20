import pytest


@pytest.fixture(autouse=True)
def use_demo_profiles(monkeypatch):
    """Существующие тесты остаются на трёх синтетических компаниях."""
    monkeypatch.setenv("PROFILE_SOURCE", "demo")
