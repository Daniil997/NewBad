import pytest


@pytest.fixture(autouse=True)
def use_demo_profiles(monkeypatch, tmp_path):
    """Keep tests on demo profiles and isolate the real-mode SQLite store per test."""
    monkeypatch.setenv("PROFILE_SOURCE", "demo")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.sqlite3"))
    from app.user_store import init_app_db

    init_app_db()
