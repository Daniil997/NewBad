from app.db import ROOT
from app.profile.build import WATCHED_FIELDS, _build_profiles, _load_yaml, load_profiles

_TOPICS = {
    "rate_borrower": "Заёмное финансирование",
    "rate_saver": "Депозиты",
    "fx_exposure": "Валютная позиция",
    "foreign_trade": "Импорт и экспорт",
    "commodity_metals": "Металлы",
    "commodity_energy": "Энергия",
    "consumption": "Потребление",
    "sector_regulation": "Регулирование",
}


def _rules() -> dict:
    return _load_yaml(ROOT / "config" / "exposures.yaml")["exposures"]


def _built(code: str, **fields: str):
    row = {field: "" for field in WATCHED_FIELDS}
    row.update(SYNTHETIC_U="U-X", MAINOKVED_CODE=code, MAINOKVED_NAME="Проверка", **fields)
    prefixes = _load_yaml(ROOT / "config" / "okved_map.yaml")["prefixes"]
    return _build_profiles([row], [], _rules(), prefixes)[0]


def _by_id(profile):
    return {item.id: item for item in profile.exposures}


def test_each_exposure_has_condition_source_and_confidence():
    for exposure_id, rule in _rules().items():
        assert rule["title"]
        assert rule["basis"] in {"confirmed", "hypothesis"}
        assert rule["source"] in {"bank_data", "derived", "payments", "client_input"}
        assert rule["confidence"] in {"high", "medium", "low"}
        assert "when" in rule
        assert "strength" in rule
        if rule["basis"] == "hypothesis":
            assert rule["assumptions"]
    for exposure_id, title in _TOPICS.items():
        assert _rules()[exposure_id]["title"] == title


def test_products_and_currency_need_direct_data():
    empty = _by_id(_built("24.10"))
    assert "rate_borrower" not in empty
    assert "rate_saver" not in empty
    assert "fx_exposure" not in empty
    assert "foreign_trade" not in empty
    assert empty["commodity_metals"].basis == "hypothesis"
    assert empty["commodity_metals"].source == "derived"

    refused = _by_id(_built("24.10", CL_PRODUCT_LOA_MARK="0", CL_PRODUCT_DEP_MARK="0", FTS_FLAG="0"))
    assert "rate_borrower" not in refused
    assert "rate_saver" not in refused
    assert "foreign_trade" not in refused

    settlement = _by_id(_built("99.99", CL_PRODUCT_RKO_MARK="1", CL_PRODUCT_GAR_MARK="1", CL_PRODUCT_ZPP_MARK="1"))
    assert set(settlement) == {"sector_regulation"}
    assert settlement["sector_regulation"].basis == "hypothesis"
    assert settlement["sector_regulation"].confidence == "low"

    trade = _by_id(_built("46.90", FTS_FLAG="1", FTS_COUNTRY_NAME="Китай"))
    assert trade["foreign_trade"].basis == "confirmed"
    assert trade["foreign_trade"].source == "bank_data"
    assert trade["foreign_trade"].feature_refs == ["FTS_FLAG"]
    assert trade["country_exposure"].basis == "confirmed"
    assert "fx_exposure" not in trade

    stated = _by_id(_built("46.90", FX_REVENUE_SHARE="40"))
    assert stated["fx_exposure"].basis == "confirmed"
    assert stated["fx_exposure"].source == "client_input"
    assert stated["fx_exposure"].feature_refs == ["FX_REVENUE_SHARE"]


def test_demo_companies_keep_confirmed_and_hypotheses_apart():
    profiles = {item.client_id: item for item in load_profiles()}
    borrower = _by_id(profiles["U-BORROWER"])
    saver = _by_id(profiles["U-SAVER"])
    peer = _by_id(profiles["U-PEER"])

    assert borrower["rate_borrower"].basis == "confirmed"
    assert borrower["rate_borrower"].confidence == "high"
    assert "rate_saver" not in borrower
    assert borrower["foreign_trade"].basis == "confirmed"
    assert "fx_exposure" not in borrower
    assert borrower["commodity_metals"].basis == "hypothesis"

    assert saver["rate_saver"].basis == "confirmed"
    assert "rate_borrower" not in saver
    assert "fx_exposure" not in saver
    assert "foreign_trade" not in saver
    assert saver["commodity_energy"].basis == "hypothesis"
    assert saver["commodity_energy"].source == "derived"

    assert "rate_borrower" not in peer
    assert "rate_saver" not in peer
    assert "fx_exposure" not in peer
    assert peer["commodity_metals"].basis == "hypothesis"
