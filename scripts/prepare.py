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
    from app.ingest.news import import_news_catalog, migrate_legacy_imports
    from app.news_store import prune_stored_news
    from app.user_store import developer_settings, init_app_db

    init_app_db()
    settings = developer_settings()
    output = ROOT / "data" / "news" / "imported"
    migrate_legacy_imports(output)
    batch = import_news_catalog(output, max_age_days=settings["news_lookback_days"])
    retention = prune_stored_news(
        retention_days=settings["news_retention_days"], max_items=settings["news_max_items"]
    )
    for source in batch.sources:
        print(
            f"{source['source']}: {source['status']}, новых {source['imported']}, "
            f"дублей {source['duplicates']}, отклонено {source['rejected']}"
        )
    print(f"SQLite: осталось новостей {retention['remaining']}")
    return 1 if batch.status == "error" else 0


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
