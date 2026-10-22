import os

import pytest
import yaml

from app.db import ROOT
from app.profile.build import load_profiles
from app.profile.case_data import appendix_paths, case_profile, clear_case_cache
from app.profile.sources import COMPANY_COLUMNS, SourceError, using_case_extract

SEGMENT_RUB = 15_000_000_000


@pytest.fixture
def case_extract(monkeypatch):
    monkeypatch.setenv("PROFILE_SOURCE", "case")
    if not using_case_extract():
        pytest.skip("Рядом нет папки DATA с CSV выгрузки")


def test_case_extract_has_borrower_and_saver(case_extract):
    payload = yaml.safe_load((ROOT / "config" / "case_clients.yaml").read_text(encoding="utf-8"))
    assert set(payload) == {"clients"}
    ids = [str(item) for item in payload["clients"]]
    borrower, saver, peer = (case_profile(item) for item in ids)
    assert borrower is not None and saver is not None and peer is not None
    assert borrower.money_role == "borrower"
    assert saver.money_role == "saver"
    assert peer.money_role == "neither"
    assert "rate_borrower" in {item.id for item in borrower.exposures}
    assert "rate_saver" in {item.id for item in saver.exposures}
    assert "rate_borrower" not in {item.id for item in saver.exposures}
    assert "rate_borrower" not in {item.id for item in peer.exposures}
    assert "rate_saver" not in {item.id for item in peer.exposures}
    assert borrower.cohort_key == peer.cohort_key
    assert borrower.cohort_share is not None and borrower.cohort_share < 1
    assert borrower.counterparties
    for profile in (borrower, saver, peer):
        revenue = next(item.value for item in profile.features if item.id == "REVENUE_COMPANY")
        assert float(revenue) >= SEGMENT_RUB
    assert [item.client_id for item in load_profiles()] == ids
    files = appendix_paths()
    assert len(files) == 9
    assert all(path.is_file() for path in files)


def test_real_case_rate_signal_personalizes_feed_and_card(case_extract, monkeypatch, tmp_path):
    from app.agent.loop import build_card
    from app.scoring.feed import build_feed

    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    clear_case_cache()
    borrower = build_feed("U_26030")
    saver = build_feed("U_99802")
    peer = build_feed("U_408263")
    assert borrower and saver and peer
    borrower_rate = next(item for item in [*borrower.top, *borrower.less_relevant] if item.news_id == "cbr-rate-14")
    saver_rate = next(item for item in [*saver.top, *saver.less_relevant] if item.news_id == "cbr-rate-14")
    peer_rate = next(item for item in [*peer.top, *peer.less_relevant] if item.news_id == "cbr-rate-14")
    assert borrower_rate.score > peer_rate.score
    assert saver_rate.score > peer_rate.score
    assert "Заёмное финансирование" in borrower_rate.reason
    assert "Депозиты" in saver_rate.reason

    borrower_card = build_card("U_26030", "cbr-rate-14")
    saver_card = build_card("U_99802", "cbr-rate-14")
    assert borrower_card and saver_card
    assert borrower_card.why_for_company.claims[0].text != saver_card.why_for_company.claims[0].text


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


def _prepare(tmp_path, monkeypatch, companies, payments, persons):
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


def test_correct_csv_loads_profile(tmp_path, monkeypatch):
    _prepare(
        tmp_path,
        monkeypatch,
        [_company("U_A", REVENUE_COMPANY="100", MAINOKVED_NAME="Металлы")],
        ["U_A;U_B;10;1"],
        ["F1;U_A"],
    )
    profile = case_profile("U_A")
    assert profile is not None
    assert profile.client_id == "U_A"
    assert _value(profile, "MAINOKVED_NAME") == "Металлы"
    assert _value(profile, "REVENUE_COMPANY") == "100"
    assert len(profile.counterparties) == 1
    assert profile.counterparties[0].client_id == "U_B"
    assert profile.counterparties[0].direction == "out"
    assert _value(profile, "related_persons") == "1"


def test_missing_file_and_wrong_schema(tmp_path, monkeypatch):
    folder = tmp_path / "DATA"
    folder.mkdir()
    monkeypatch.setenv("PROFILE_SOURCE", "case")
    monkeypatch.setenv("CASE_DATA_DIR", str(folder))
    clear_case_cache()
    with pytest.raises(SourceError) as raised:
        case_profile("U_A")
    assert raised.value.kind == "missing"

    (folder / "df_Uid_data_hack.csv").write_text("A;B\n1;2\n", encoding="utf-8")
    clear_case_cache()
    with pytest.raises(SourceError) as raised:
        case_profile("U_A")
    assert raised.value.kind == "schema"


def test_unknown_company_id(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch, [_company("U_A")], ["U_A;U_B;1;1"], ["F1;U_A"])
    assert case_profile("NOBODY") is None
    assert case_profile("  ") is None
    assert case_profile("U_A") is not None


def test_payments_both_directions_and_link_count(tmp_path, monkeypatch):
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
    assert len(profile.counterparties) == 3
    assert links[("U_B", "out")].pay_amt == 10
    assert links[("U_B", "out")].pay_cnt == 1
    assert links[("U_B", "out")].same_group is True
    assert links[("U_C", "in")].pay_amt == 7
    assert links[("U_C", "in")].same_group is False
    assert ("U_A", "out") not in links
    assert links[("U_D", "out")].pay_amt is None
    assert "U_E" not in {item.client_id for item in profile.counterparties}
    assert _value(profile, "related_persons") == "2"


def test_missing_and_invalid_revenue(tmp_path, monkeypatch):
    _prepare(
        tmp_path,
        monkeypatch,
        [
            _company("U_OK", REVENUE_COMPANY="100"),
            _company("U_EMPTY", REVENUE_COMPANY="", GROUP_ID=""),
            _company("U_BAD", REVENUE_COMPANY="не число", GROUP_ID=""),
        ],
        ["U_OK;U_EMPTY;1;1"],
        ["F1;U_OK"],
    )
    empty = case_profile("U_EMPTY")
    invalid = case_profile("U_BAD")
    assert empty is not None and invalid is not None
    assert _value(empty, "REVENUE_COMPANY") is None
    assert empty.cohort_share is None
    assert "REVENUE_COMPANY" in empty.missing_data
    assert _value(invalid, "REVENUE_COMPANY") == "не число"
    assert invalid.cohort_share is None


def test_revenue_and_payments_are_not_double_counted(tmp_path, monkeypatch):
    _prepare(
        tmp_path,
        monkeypatch,
        [
            _company("U_A", REVENUE_COMPANY="100"),
            _company("U_A", REVENUE_COMPANY="100"),
            _company("U_B", REVENUE_COMPANY="300"),
            _company("U_C", REVENUE_COMPANY="1", GROUP_ID="G"),
            _company("U_C", REVENUE_COMPANY="9", GROUP_ID="H"),
        ],
        ["U_A;U_B;10;1", "U_A;U_B;10;1"],
        ["F1;U_A"],
    )
    first = case_profile("U_A")
    second = case_profile("U_B")
    conflict = case_profile("U_C")
    assert first is not None and second is not None and conflict is not None
    assert _value(first, "REVENUE_COMPANY") == "100"
    assert first.cohort_share == 0.25
    assert len(first.counterparties) == 1
    assert first.counterparties[0].pay_amt == 10
    assert _value(first, "group_rank") == "2"
    assert _value(first, "group_size") == "2"
    assert _value(second, "group_rank") == "1"
    assert _value(conflict, "REVENUE_COMPANY") is None
    assert conflict.cohort_share is None


def test_cache_follows_company_file(tmp_path, monkeypatch):
    folder = _prepare(tmp_path, monkeypatch, [_company("U_A", REVENUE_COMPANY="100")], ["U_A;U_B;1;1"], ["F1;U_A"])
    assert _value(case_profile("U_A"), "REVENUE_COMPANY") == "100"
    path = folder / "df_Uid_data_hack.csv"
    stamp = path.stat().st_mtime_ns
    _write_companies(path, [_company("U_A", REVENUE_COMPANY="400")])
    os.utime(path, ns=(stamp, stamp))
    assert _value(case_profile("U_A"), "REVENUE_COMPANY") == "100"
    os.utime(path, ns=(stamp, stamp + 1_000_000_000))
    assert _value(case_profile("U_A"), "REVENUE_COMPANY") == "400"
