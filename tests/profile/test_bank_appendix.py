import pytest
import yaml

from app.profile.bank_catalog import BankCatalogError, load_bank_appendix
from app.profile.sources import bank_pdf_dir

_ROW = {
    "id": "key_rate",
    "file": "rate.pdf",
    "title": "Ключевая ставка",
    "date": "2026-09-11",
    "type": "monetary_policy",
    "topic": "key_rate",
    "source": "bank_analytics",
}


def _write(folder, catalog, row):
    folder.mkdir()
    (folder / row["file"]).write_bytes(b"%PDF")
    catalog.write_text(yaml.safe_dump({"documents": [row]}, allow_unicode=True), encoding="utf-8")


def test_listed_file_is_not_processed(tmp_path):
    folder = tmp_path / "pdf"
    catalog = tmp_path / "catalog.yaml"
    row = {**_ROW, "processed": True}
    _write(folder, catalog, row)
    items = load_bank_appendix(catalog, folder)
    assert len(items) == 1
    assert items[0].processed is False
    assert items[0].path.is_file()
    assert items[0].source == "bank_analytics"
    assert items[0].date == "2026-09-11"


def test_missing_file_and_unknown_type_are_errors(tmp_path):
    folder = tmp_path / "pdf"
    folder.mkdir()
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(yaml.safe_dump({"documents": [_ROW]}, allow_unicode=True), encoding="utf-8")
    with pytest.raises(BankCatalogError) as raised:
        load_bank_appendix(catalog, folder)
    assert raised.value.kind == "missing"
    assert "rate.pdf" in str(raised.value)

    broken = {**_ROW, "type": "not_a_type"}
    (folder / "rate.pdf").write_bytes(b"%PDF")
    catalog.write_text(yaml.safe_dump({"documents": [broken]}, allow_unicode=True), encoding="utf-8")
    with pytest.raises(BankCatalogError) as raised:
        load_bank_appendix(catalog, folder)
    assert raised.value.kind == "schema"

    incomplete = {key: value for key, value in _ROW.items() if key != "title"}
    catalog.write_text(yaml.safe_dump({"documents": [incomplete]}, allow_unicode=True), encoding="utf-8")
    with pytest.raises(BankCatalogError) as raised:
        load_bank_appendix(catalog, folder)
    assert raised.value.kind == "schema"


def test_real_appendix_lists_nine_files_without_processing():
    folder = bank_pdf_dir()
    if folder is None:
        pytest.skip("Рядом нет папки DATA/bank_analytics или Doc/Данные данные")
    items = load_bank_appendix()
    assert len(items) == 9
    assert {item.id for item in items} == {
        "key_rate",
        "inflation",
        "bond_brief",
        "gold",
        "strategy_3q",
        "macro_7m",
        "consumption",
        "macro_1h",
        "steels",
    }
    assert all(item.path.is_file() for item in items)
    assert all(item.processed is False for item in items)
    assert all(item.source == "bank_analytics" and item.title and item.type for item in items)
    by_id = {item.id: item for item in items}
    assert by_id["key_rate"].date == "2026-09-11"
    assert by_id["consumption"].date == "unknown"
    assert by_id["steels"].date == "2021-04-15"
    assert by_id["inflation"].date == "2026-09-02"
