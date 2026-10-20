from fastapi.testclient import TestClient

from app.db import ROOT
from app.main import app
from app.profile.build import WATCHED_FIELDS, _build_profiles, _load_yaml, load_profiles


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


def _built(code: str, **fields: str):
    rules = _load_yaml(ROOT / "config" / "exposures.yaml")["exposures"]
    prefixes = _load_yaml(ROOT / "config" / "okved_map.yaml")["prefixes"]
    row = {field: "" for field in WATCHED_FIELDS}
    row.update(SYNTHETIC_U="U-X", MAINOKVED_CODE=code, MAINOKVED_NAME="Проверка", **fields)
    return _build_profiles([row], [], rules, prefixes)[0]


def test_profile_keeps_facts_calculations_and_hypotheses_apart():
    borrower = next(item for item in load_profiles() if item.client_id == "U-BORROWER")
    facts = {item.id: item for item in borrower.features}

    assert facts["MAINOKVED_NAME"].value == "Производство чугуна и стали"
    assert facts["MAINOKVED_NAME"].origin == "bank_data"
    assert facts["MAINOKVED_NAME"].source_fields == ["MAINOKVED_NAME"]
    assert facts["COMPANY_AGE_YEARS_GRADATION"].value == "10-15 лет"
    assert facts["AMOUNT_WORKERS_FNS_GRADATION"].value == "1000-5000"
    assert facts["REVENUE_COMPANY"].value == "20000000000"
    assert facts["CITY"].value == "Москва"
    assert "REGION" not in facts
    assert facts["CL_PRODUCT_LOA_MARK"].value == "есть"
    assert facts["CL_PRODUCT_LOA_MARK"].source_fields == ["CL_PRODUCT_LOA_MARK"]
    assert facts["CL_PRODUCT_DEP_MARK"].value == "нет"
    assert "CL_PRODUCT_DEP_MARK" not in borrower.missing_data
    assert facts["okved_theme"].value == "metals"
    assert facts["okved_theme"].origin == "derived"
    assert facts["okved_theme"].source_fields == ["MAINOKVED_CODE"]

    borrower_rule = next(item for item in borrower.exposures if item.id == "rate_borrower")
    assert "CL_PRODUCT_LOA_MARK" in borrower_rule.feature_refs
    assert "CL_PRODUCT_LEA_MARK" in borrower_rule.feature_refs
    assert borrower_rule.assumptions
    assert all(item.value != borrower_rule.assumptions[0] for item in borrower.features)
    assert "rate_saver" not in {item.id for item in borrower.exposures}


def test_unknown_industry_does_not_invent_exposure():
    unknown = _built("99.99")
    assert all(item.id != "okved_theme" for item in unknown.features)
    assert "REVENUE_COMPANY" in unknown.missing_data
    assert "CL_PRODUCT_LOA_MARK" in unknown.missing_data
    assert all(item.value not in {"0", "0.0", "нет"} for item in unknown.features)
    assert unknown.cohort_share is None
    ids = {item.id for item in unknown.exposures}
    assert "commodity_metals" not in ids
    assert "commodity_energy" not in ids
    assert "commodity_precious" not in ids
    assert "consumption" not in ids
    assert "rate_borrower" not in ids

    precious = _built("24.41")
    theme = next(item for item in precious.features if item.id == "okved_theme")
    assert theme.value == "precious_metals"
    assert theme.origin == "derived"
    assert theme.source_fields == ["MAINOKVED_CODE"]
    precious_ids = {item.id for item in precious.exposures}
    assert "commodity_precious" in precious_ids
    assert "commodity_metals" not in precious_ids


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
