import logging

import pytest

from app.profile.sources import (
    COMPANY_COLUMNS,
    PAYMENT_COLUMNS,
    SourceError,
    companies_csv,
    describe_csv,
    read_csv,
)

_COMPANY_HEADER = ";".join(sorted(COMPANY_COLUMNS))


def _company_csv(path, body: str) -> None:
    path.write_text(_COMPANY_HEADER + "\n" + body, encoding="utf-8")


def test_case_data_dir_reads_semicolon_and_blanks(tmp_path, monkeypatch, caplog):
    folder = tmp_path / "DATA"
    folder.mkdir()
    values = {name: "" for name in COMPANY_COLUMNS}
    values["SYNTHETIC_U"] = "U_1"
    values["MAINOKVED_NAME"] = "Торговля"
    line = ";".join(values[name] for name in sorted(COMPANY_COLUMNS))
    _company_csv(folder / "df_Uid_data_hack.csv", line + "\n")
    monkeypatch.setenv("PROFILE_SOURCE", "case")
    monkeypatch.setenv("CASE_DATA_DIR", str(folder))
    with caplog.at_level(logging.INFO):
        path = companies_csv()
        rows = read_csv(path, COMPANY_COLUMNS)
    assert path.name == "df_Uid_data_hack.csv"
    assert rows[0]["SYNTHETIC_U"] == "U_1"
    assert rows[0]["CITY"] == ""
    assert rows[0]["MAINOKVED_NAME"] == "Торговля"
    assert "U_1" not in caplog.text
    assert "columns=" in caplog.text


def test_missing_configured_folder_is_not_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_SOURCE", "case")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path / "missing"))
    with pytest.raises(SourceError) as raised:
        companies_csv()
    assert raised.value.kind == "missing"


def test_empty_file_wrong_schema_and_no_rows(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SourceError) as raised:
        describe_csv(empty, PAYMENT_COLUMNS)
    assert raised.value.kind == "empty"

    wrong = tmp_path / "wrong.csv"
    wrong.write_text("A;B\n1;2\n", encoding="utf-8")
    with pytest.raises(SourceError) as raised:
        describe_csv(wrong, PAYMENT_COLUMNS)
    assert raised.value.kind == "schema"

    header_only = tmp_path / "header.csv"
    header_only.write_text("PAYER;BENEFICIARY;PAY_AMT;PAY_CNT\n", encoding="utf-8")
    with pytest.raises(SourceError) as raised:
        read_csv(header_only, PAYMENT_COLUMNS)
    assert raised.value.kind == "not_found"


def test_cp1251_value_is_read(tmp_path):
    path = tmp_path / "payments.csv"
    path.write_bytes("PAYER;BENEFICIARY;PAY_AMT;PAY_CNT\nU1;ООО;10;1\n".encode("cp1251"))
    rows = read_csv(path, PAYMENT_COLUMNS)
    assert rows == [{"PAYER": "U1", "BENEFICIARY": "ООО", "PAY_AMT": "10", "PAY_CNT": "1"}]
