import yaml
import pytest
from fastapi.testclient import TestClient

from app.db import ROOT
from app.main import app
from app.profile.case_data import clear_case_cache
from app.profile.sources import case_dir, using_case_extract
from app.schemas.card import Card
from eval.metrics import source_supported_share, unsupported_claim_ids


def test_unseen_pasted_news_stays_grounded_and_outside_tuning_set(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("PROFILE_SOURCE", "case")
    if not using_case_extract() or case_dir() is None:
        pytest.skip("Рядом нет папки DATA с выгрузкой компаний кейса")
    clear_case_cache()
    holdout = yaml.safe_load((ROOT / "eval" / "holdout_news.yaml").read_text(encoding="utf-8"))
    assert holdout["status"] == "pending_expert_review"
    tuning = yaml.safe_load((ROOT / "eval" / "personalization_cases.yaml").read_text(encoding="utf-8"))
    tuned_ids = {item["id"] for item in tuning["news"]}
    assert all(case["id"] not in tuned_ids for case in holdout["cases"])

    with TestClient(app) as client:
        for case in holdout["cases"]:
            response = client.post(
                f"/api/clients/{case['company_id']}/news",
                json={"title": case["title"], "text": case["text"]},
            )
            assert response.status_code == 200
            card = Card.model_validate(response.json())
            assert card.event_id.startswith("pasted-")
            assert card.verification.status == "passed"
            assert source_supported_share(card) == 1.0
            assert unsupported_claim_ids(card) == []
            if case["expected_exposure"]:
                assert case["expected_exposure"] in card.features_triggered
            else:
                assert card.features_triggered == []
