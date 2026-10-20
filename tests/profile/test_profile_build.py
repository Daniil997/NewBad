from app.db import ROOT
from app.profile.build import FIELD_LABELS, FLAG_FIELDS, WATCHED_FIELDS, _build_profiles, _load_yaml, load_profiles
from app.profile.sources import read_csv


def _built(code: str, **fields: str):
    rules = _load_yaml(ROOT / "config" / "exposures.yaml")["exposures"]
    prefixes = _load_yaml(ROOT / "config" / "okved_map.yaml")["prefixes"]
    row = {field: "" for field in WATCHED_FIELDS}
    row.update(SYNTHETIC_U="U-X", MAINOKVED_CODE=code, MAINOKVED_NAME="Проверка", **fields)
    return _build_profiles([row], [], rules, prefixes)[0]


def _ids(profile) -> set[str]:
    return {item.id for item in profile.exposures}


def test_profile_matches_source_row():
    rows = read_csv(ROOT / "data" / "demo" / "companies.csv")
    profiles = {item.client_id: item for item in load_profiles()}
    for row in rows:
        profile = profiles[row["SYNTHETIC_U"]]
        facts = {item.id: item for item in profile.features}
        for field in FIELD_LABELS:
            raw = (row.get(field) or "").strip()
            if not raw:
                assert field not in facts
                fts_off = field == "FTS_COUNTRY_NAME" and (row.get("FTS_FLAG") or "").strip() in {"0", "0.0"}
                if fts_off:
                    assert field not in profile.missing_data
                else:
                    assert field in profile.missing_data
                continue
            assert facts[field].origin == "bank_data"
            assert facts[field].source_fields == [field]
            if field in FLAG_FIELDS:
                assert facts[field].value == ("есть" if raw in {"1", "1.0"} else "нет")
            else:
                assert facts[field].value == raw


def test_gaps_stay_gaps():
    profile = _built("24.10")
    for field in ("REVENUE_COMPANY", "REGION", "CL_PRODUCT_LOA_MARK", "AMOUNT_WORKERS_FNS_GRADATION"):
        assert field in profile.missing_data
        assert all(item.id != field for item in profile.features)
    assert profile.cohort_share is None
    assert all(item.value not in {"0", "0.0"} for item in profile.features)

    explicit = _built("24.10", CL_PRODUCT_DEP_MARK="0", REVENUE_COMPANY="100")
    deposit = next(item for item in explicit.features if item.id == "CL_PRODUCT_DEP_MARK")
    assert deposit.value == "нет"
    assert deposit.source_fields == ["CL_PRODUCT_DEP_MARK"]
    assert "CL_PRODUCT_DEP_MARK" not in explicit.missing_data
    assert next(item.value for item in explicit.features if item.id == "REVENUE_COMPANY") == "100"


def test_industry_and_exposures_follow_config():
    borrower = _built("24.10", CL_PRODUCT_LOA_MARK="1", CL_PRODUCT_LEA_MARK="1", CL_PRODUCT_DEP_MARK="0")
    theme = next(item for item in borrower.features if item.id == "okved_theme")
    assert theme.value == "metals"
    assert theme.origin == "derived"
    assert theme.source_fields == ["MAINOKVED_CODE"]
    assert "commodity_metals" in _ids(borrower)
    assert "commodity_precious" not in _ids(borrower)
    assert "rate_borrower" in _ids(borrower)
    assert "rate_saver" not in _ids(borrower)
    rule = next(item for item in borrower.exposures if item.id == "rate_borrower")
    assert set(rule.feature_refs) == {"CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK"}
    assert rule.assumptions
    assert all(item.value != rule.assumptions[0] for item in borrower.features)

    precious = _built("24.41")
    assert next(item.value for item in precious.features if item.id == "okved_theme") == "precious_metals"
    assert "commodity_precious" in _ids(precious)
    assert "commodity_metals" not in _ids(precious)

    saver = _built("46.71", CL_PRODUCT_DEP_MARK="1", CL_PRODUCT_LOA_MARK="0", CL_PRODUCT_LEA_MARK="0")
    assert next(item.value for item in saver.features if item.id == "okved_theme") == "trade"
    assert "consumption" in _ids(saver)
    assert "rate_saver" in _ids(saver)
    assert "rate_borrower" not in _ids(saver)


def test_unknown_industry_has_no_unconfirmed_exposure():
    unknown = _built("99.99")
    assert all(item.id != "okved_theme" for item in unknown.features)
    assert "REVENUE_COMPANY" in unknown.missing_data
    assert "CL_PRODUCT_LOA_MARK" in unknown.missing_data
    assert unknown.cohort_share is None
    assert "commodity_metals" not in _ids(unknown)
    assert "commodity_energy" not in _ids(unknown)
    assert "commodity_precious" not in _ids(unknown)
    assert "consumption" not in _ids(unknown)
    assert "rate_borrower" not in _ids(unknown)
    assert "sector_regulation" in _ids(unknown)
