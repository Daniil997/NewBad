"""Подготовка данных.

По умолчанию только проверка демо-набора: без сети, без OCR и без чтения закрытых CSV.
Импорт новостей и PDF запускается отдельными командами.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def check() -> int:
    news = sorted(path for path in (ROOT / "data" / "news").glob("*.json") if path.name != "import.json")
    bank = sorted((ROOT / "data" / "bank").glob("*.json"))
    companies = ROOT / "data" / "demo" / "companies.csv"
    print(f"демо-компании: {'есть' if companies.is_file() else 'нет'}")
    print(f"новости: {len(news)}")
    print(f"паспорта банка: {len(bank)}")
    if companies.is_file() and news and bank:
        print("демо-набор на месте, ключ и видеокарта не нужны")
        return 0
    print("демо-набор неполный")
    return 1


def import_rss() -> int:
    from app.ingest.news import NewsIngestError, import_news

    try:
        batch = import_news(ROOT / "data" / "news" / "imported")
    except NewsIngestError as error:
        print(f"импорт новостей не выполнен: {error}")
        return 1
    accepted = sum(1 for item in batch.items if item.status != "duplicate")
    print(f"импорт новостей: статус {batch.status}, принято {accepted}, отклонено {len(batch.rejected)}")
    return 0


def import_bank() -> int:
    from app.ingest.bank_documents import ingest_bank_documents
    from app.profile.sources import bank_pdf_dir

    folder = bank_pdf_dir()
    if folder is None:
        print("папка PDF не найдена, импорт банка пропущен")
        return 1
    documents = ingest_bank_documents(ROOT / "data" / "bank" / "extracted", folder=folder)
    print(f"импорт банка: документов {len(documents)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка демо-набора и импорт источников")
    parser.add_argument("command", nargs="?", choices=("check", "news", "bank"), default="check")
    args = parser.parse_args(argv)
    if args.command == "news":
        return import_rss()
    if args.command == "bank":
        return import_bank()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
