from fastapi.testclient import TestClient

from app.main import app


def test_health_creates_empty_journal(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "newbad"
    assert body["journal"] == ["llm_calls", "tool_calls"]
    assert body["journal_empty"] is True
    assert (tmp_path / "journal.sqlite3").is_file()
