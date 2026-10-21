import pytest

from app.profile.build import _okved_digits, _okved_rules, _theme, load_profiles
from app.profile.case_data import case_profile
from app.profile.sources import SourceError, companies_csv, using_case_extract


def _theme_of(profile) -> str | None:
    found = [item.value for item in profile.features if item.id == "okved_theme"]
    return found[0] if found else None


def _code(profile) -> str:
    return next(item.value for item in profile.features if item.id == "MAINOKVED_CODE")


def test_map_does_not_use_a_whole_mixed_class():
    prefixes, ambiguous = _okved_rules()
    digits = {_okved_digits(prefix) for prefix in prefixes}
    assert "46" not in digits
    assert "64" not in digits
    assert "0729" not in digits
    assert "467" not in digits
    assert {"4671", "4672", "2441", "07291", "4730", "4690"} <= digits
    assert _okved_digits("64.20") in {_okved_digits(item) for item in ambiguous}
    assert _okved_digits("24.46") in {_okved_digits(item) for item in ambiguous}


def test_specific_code_beats_a_broader_industry():
    prefixes, ambiguous = _okved_rules()
    assert _theme("46.71.2", prefixes, ambiguous) == "energy"
    assert _theme("46.72", prefixes, ambiguous) == "metals"
    assert _theme("46.90", prefixes, ambiguous) == "trade"
    assert _theme("46.38", prefixes, ambiguous) == "food"
    assert _theme("46.21", prefixes, ambiguous) == "agro"
    assert _theme("47.30", prefixes, ambiguous) == "energy"
    assert _theme("47.71", prefixes, ambiguous) == "trade"
    assert _theme("24.10", prefixes, ambiguous) == "metals"
    assert _theme("24.41", prefixes, ambiguous) == "precious_metals"
    assert _theme("07.29.1", prefixes, ambiguous) == "precious_metals"
    assert _theme("07.29.2", prefixes, ambiguous) == "metals"
    assert _theme("46.73", prefixes, ambiguous) is None
    assert _theme("46.75", prefixes, ambiguous) is None


def test_unknown_and_ambiguous_codes_get_no_theme():
    prefixes, ambiguous = _okved_rules()
    assert _theme("", prefixes, ambiguous) is None
    assert _theme("99.99", prefixes, ambiguous) is None
    assert _theme("64.20", prefixes, ambiguous) is None
    assert _theme("64.99", prefixes, ambiguous) == "finance"
    assert _theme("24.46", prefixes, ambiguous) is None
    assert _theme("46.1", prefixes, ambiguous) is None
    assert _theme("46.76.1", prefixes, ambiguous) is None
    assert _theme("46.77", prefixes, ambiguous) is None


def test_demo_companies_follow_their_okved():
    profiles = {item.client_id: item for item in load_profiles()}
    assert _code(profiles["U-BORROWER"]) == "24.10"
    assert _theme_of(profiles["U-BORROWER"]) == "metals"
    assert _code(profiles["U-SAVER"]) == "46.71"
    assert _theme_of(profiles["U-SAVER"]) == "energy"
    assert _code(profiles["U-PEER"]) == "24.20"
    assert _theme_of(profiles["U-PEER"]) == "metals"


def test_showcase_companies_follow_their_okved(monkeypatch):
    monkeypatch.setenv("PROFILE_SOURCE", "case")
    if not using_case_extract():
        pytest.skip("Рядом нет папки DATA с CSV выгрузки")
    try:
        if not companies_csv().is_file():
            pytest.skip("Рядом нет папки DATA с CSV выгрузки")
    except SourceError:
        pytest.skip("Рядом нет папки DATA с CSV выгрузки")

    borrower = case_profile("U_26030")
    saver = case_profile("U_99802")
    peer = case_profile("U_408263")
    assert borrower is not None and saver is not None and peer is not None
    assert _code(borrower) == "46.71.2"
    assert _theme_of(borrower) == "energy"
    assert "commodity_energy" in {item.id for item in borrower.exposures}
    assert "consumption" not in {item.id for item in borrower.exposures}
    assert _code(saver) == "68.20.2"
    assert _theme_of(saver) == "real_estate"
    assert _code(peer) == "46.90"
    assert _theme_of(peer) == "trade"
    assert "consumption" in {item.id for item in peer.exposures}
    assert "commodity_energy" not in {item.id for item in peer.exposures}
