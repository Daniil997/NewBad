import pytest


@pytest.fixture(autouse=True)
def isolated_databases(monkeypatch, tmp_path):
    """Tests never write accounts, collected data or LLM logs to the working database."""
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "journal"))
    from app.user_store import init_app_db
    init_app_db()
