import os

import pytest

from app.profile.build import load_profiles
from app.profile.case_data import appendix_paths, case_profile, clear_case_cache
from app.profile.sources import COMPANY_COLUMNS, using_case_extract


@pytest.fixture
def case_extract(monkeypatch):
    monkeypatch.setenv("PROFILE_SOURCE", "case")
    if not using_case_extract():
        pytest.skip("Рядом нет папки DATA с CSV выгрузки")


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


def _write_companies(path, rows: list[dict[str, str]]) -> None:
    header = sorted(COMPANY_COLUMNS)
    lines = [";".join(header)]
    for row in rows:
        lines.append(";".join(row.get(name, "") for name in header))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _company(client_id: str, **extra: str) -> dict[str, str]:
    row = {
        "SYNTHETIC_U": client_id,
        "MAINOKVED_CODE": "24.10",
        "MAINOKVED_NAME": "Металлы",
        "REVENUE_COMPANY": "100",
        "GROUP_ID": "G",
    }
    row.update(extra)
    return row


def _prepare(tmp_path, monkeypatch, companies, payments, persons) -> object:
    folder = tmp_path / "DATA"
    folder.mkdir()
    _write_companies(folder / "df_Uid_data_hack.csv", companies)
    payment_lines = ["PAYER;BENEFICIARY;PAY_AMT;PAY_CNT", *payments]
    (folder / "df_Uid_payment_hack.csv").write_text("\n".join(payment_lines) + "\n", encoding="utf-8")
    person_lines = ["SYNTHETIC_F;SYNTHETIC_U", *persons]
    (folder / "df_Fid_Uid_link_hack.csv").write_text("\n".join(person_lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("PROFILE_SOURCE", "case")
    monkeypatch.setenv("CASE_DATA_DIR", str(folder))
    clear_case_cache()
    return folder


def _value(profile, feature_id: str) -> str | None:
    found = [item.value for item in profile.features if item.id == feature_id]
    return found[0] if found else None


def test_payments_keep_direction_without_double_count(tmp_path, monkeypatch):
    _prepare(
        tmp_path,
        monkeypatch,
        [
            _company("U_A", GROUP_ID="G"),
            _company("U_B", GROUP_ID="G"),
            _company("U_C", GROUP_ID="OTHER"),
            _company("U_D", GROUP_ID=""),
        ],
        [
            "U_A;U_B;10;1",
            "U_A;U_B;10;1",
            "U_C;U_A;7;2",
            "U_A;U_A;99;1",
            "U_A;U_D;10;1",
            "U_A;U_D;15;1",
        ],
        ["F1;U_A", "F1;U_A", "F2;U_A", "F1;U_E"],
    )
    profile = case_profile("U_A")
    assert profile is not None
    links = {(item.client_id, item.direction): item for item in profile.counterparties}
    assert links[("U_B", "out")].pay_amt == 10
    assert links[("U_B", "out")].pay_cnt == 1
    assert links[("U_B", "out")].same_group is True
    assert links[("U_C", "in")].pay_amt == 7
    assert links[("U_C", "in")].same_group is False
    assert ("U_A", "out") not in links
    assert links[("U_D", "out")].pay_amt is None
    assert "U_E" not in {item.client_id for item in profile.counterparties}
    assert _value(profile, "related_persons") == "2"


def test_company_revenue_is_not_summed_with_group_or_duplicates(tmp_path, monkeypatch):
    _prepare(
        tmp_path,
        monkeypatch,
        [
            _company("U_A", REVENUE_COMPANY="100"),
            _company("U_A", REVENUE_COMPANY="100"),
            _company("U_B", REVENUE_COMPANY="300"),
            _company("U_C", REVENUE_COMPANY="1", GROUP_ID="G"),
            _company("U_C", REVENUE_COMPANY="9", GROUP_ID="H"),
            _company("U_E", REVENUE_COMPANY="", GROUP_ID=""),
        ],
        ["U_A;U_B;1;1"],
        ["F1;U_A"],
    )
    first = case_profile("U_A")
    second = case_profile("U_B")
    conflict = case_profile("U_C")
    empty = case_profile("U_E")
    assert first is not None and second is not None and conflict is not None and empty is not None
    assert _value(first, "REVENUE_COMPANY") == "100"
    assert first.cohort_share == 0.25
    assert _value(first, "group_rank") == "2"
    assert _value(first, "group_size") == "2"
    assert _value(second, "group_rank") == "1"
    assert _value(conflict, "REVENUE_COMPANY") is None
    assert conflict.cohort_share is None
    assert "REVENUE_COMPANY" in conflict.missing_data
    assert _value(empty, "REVENUE_COMPANY") is None
    assert empty.cohort_share is None
    assert "REVENUE_COMPANY" in empty.missing_data


def test_unknown_id_and_cache_follows_file(tmp_path, monkeypatch):
    folder = _prepare(
        tmp_path,
        monkeypatch,
        [_company("U_A", REVENUE_COMPANY="100")],
        ["U_A;U_B;1;1"],
        ["F1;U_A"],
    )
    assert case_profile("NOBODY") is None
    assert case_profile("  ") is None
    profile = case_profile("U_A")
    assert profile is not None
    assert _value(profile, "REVENUE_COMPANY") == "100"

    path = folder / "df_Uid_data_hack.csv"
    stamp = path.stat().st_mtime_ns
    _write_companies(path, [_company("U_A", REVENUE_COMPANY="400")])
    os.utime(path, ns=(stamp, stamp))
    assert _value(case_profile("U_A"), "REVENUE_COMPANY") == "100"
    os.utime(path, ns=(stamp, stamp + 1_000_000_000))
    assert _value(case_profile("U_A"), "REVENUE_COMPANY") == "400"
