from fastapi.testclient import TestClient

from app.main import app
from app.profile.build import load_profiles


def test_borrower_and_saver_have_different_exposures():
    profiles = {item.client_id: item for item in load_profiles()}
    borrower = profiles["U-BORROWER"]
    saver = profiles["U-SAVER"]

    borrower_ids = {item.id for item in borrower.exposures}
    saver_ids = {item.id for item in saver.exposures}

    assert "rate_borrower" in borrower_ids
    assert "rate_saver" not in borrower_ids
    assert "rate_saver" in saver_ids
    assert "rate_borrower" not in saver_ids
    assert borrower.money_role == "borrower"
    assert saver.money_role == "saver"


def test_missing_fields_stay_empty():
    borrower = next(item for item in load_profiles() if item.client_id == "U-BORROWER")
    assert "AMOUNT_WORKERS_SPARK_GRADATION" in borrower.missing_data
    assert "REGION" in borrower.missing_data
    assert all(feature.id != "AMOUNT_WORKERS_SPARK_GRADATION" for feature in borrower.features)
    assert all(feature.value != "" for feature in borrower.features)

    saver = next(item for item in load_profiles() if item.client_id == "U-SAVER")
    assert "COMPANY_AGE_YEARS_GRADATION" in saver.missing_data
    assert "FTS_COUNTRY_NAME" not in saver.missing_data


def test_cohort_share_and_payments():
    borrower = next(item for item in load_profiles() if item.client_id == "U-BORROWER")
    assert borrower.cohort_share == 0.25
    peers = {item.client_id for item in borrower.counterparties}
    assert "U-PEER" in peers
    inside = next(item for item in borrower.counterparties if item.client_id == "U-PEER")
    outside = next(item for item in borrower.counterparties if item.client_id == "U-SAVER")
    assert inside.same_group is True
    assert outside.same_group is False


def test_client_api():
    with TestClient(app) as client:
        listing = client.get("/api/clients")
        assert listing.status_code == 200
        ids = {item["client_id"] for item in listing.json()}
        assert {"U-BORROWER", "U-SAVER", "U-PEER"} <= ids

        profile = client.get("/api/clients/U-SAVER")
        assert profile.status_code == 200
        assert profile.json()["money_role"] == "saver"

        missing = client.get("/api/clients/no-such")
        assert missing.status_code == 404
