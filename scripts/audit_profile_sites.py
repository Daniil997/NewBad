"""Opt-in live profile audit using fresh accounts and an isolated SQLite database.

Run from NewBad: python scripts/audit_profile_sites.py --output .qa/sites-before.json
Uses the configured profile model and makes real requests to public company sites.
"""

import argparse
import json
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.user_store import developer_settings, save_developer_settings  # noqa: E402
from app.profile import site_extract  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sites", nargs="+", default=[
        "https://mts.ru", "https://www.kaspersky.ru", "https://www.magnit.com/ru/",
    ])
    args = parser.parse_args()
    settings = developer_settings()
    settings.update(news_collection_interval_hours=0, market_collection_interval_hours=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="profile-audit-", dir=args.output.parent))
    os.environ.update(APP_DB_PATH=str(work.resolve() / "app.sqlite3"),
                      DATA_DIR=str(work.resolve()), AUTH_REGISTRATION_ENABLED="true")
    results = []
    model_responses = []
    original_complete = site_extract.complete_tier
    def record_completion(*args, **kwargs):
        outcome = original_complete(*args, **kwargs)
        model_responses.append(outcome.text)
        return outcome
    site_extract.complete_tier = record_completion
    with TestClient(app) as client:
        save_developer_settings(settings)
        for index, website in enumerate(args.sites):
            client.cookies.clear()
            def csrf():
                return {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}
            registered = client.post("/api/auth/register", headers=csrf(), json={
                "login": f"site_audit_{index}", "password": secrets.token_urlsafe(24),
            })
            assert registered.status_code == 201, registered.text
            saved = client.put("/api/real/profile", headers=csrf(), json={"website": website})
            assert saved.status_code == 200, saved.text
            started = time.monotonic()
            model_responses.clear()
            response = client.post("/api/real/profile/site-analysis", headers=csrf(), json={"consent": True})
            result = {"website": website, "http_status": response.status_code,
                      "seconds": round(time.monotonic() - started, 1), "analysis": response.json(),
                      "model_responses": list(model_responses)}
            if response.status_code == 200:
                before = client.get("/api/real/profile").json()["profile"]
                assert before["company_name"] == "", "Scan modified a profile without acceptance"
                accepted = client.post("/api/real/profile/suggestions/apply", headers=csrf())
                assert accepted.status_code == 200, accepted.text
                result["accepted_fields"] = accepted.json()["filled_fields"]
                sources = client.get("/api/real/profile/sources").json()["sources"]
                result["sources_persisted"] = len(sources)
                assert len(sources) == len(result["accepted_fields"])
                assert not client.get("/api/real/profile/suggestions").json()["suggestions"]
                result["profile"] = client.get("/api/real/profile").json()["profile"]
            results.append(result)
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in result.items() if k not in {"analysis", "profile", "model_responses"}},
                             ensure_ascii=False), flush=True)
    print(f"Report: {args.output.resolve()}", flush=True)


if __name__ == "__main__":
    main()
