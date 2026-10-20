import pytest

from app.profile.build import load_profiles
from app.profile.case_data import appendix_paths, case_profile
from app.profile.sources import using_case_extract


@pytest.fixture
def case_extract(monkeypatch):
    monkeypatch.setenv("PROFILE_SOURCE", "case")
    if not using_case_extract():
        pytest.skip("Рядом нет папки Doc/Данные данные")


def test_case_extract_has_borrower_and_saver(case_extract):
    borrower = case_profile("U_23929")
    saver = case_profile("U_12470")
    assert borrower is not None and saver is not None
    assert borrower.money_role == "borrower"
    assert saver.money_role == "saver"
    assert "rate_borrower" in {item.id for item in borrower.exposures}
    assert "rate_saver" in {item.id for item in saver.exposures}
    assert "rate_borrower" not in {item.id for item in saver.exposures}
    assert borrower.cohort_share is not None and borrower.cohort_share < 1
    assert borrower.counterparties
    ids = [item.client_id for item in load_profiles()]
    assert ids == ["U_23929", "U_12470", "U_416505"]
    files = appendix_paths()
    assert len(files) == 9
    assert all(path.is_file() for path in files)
