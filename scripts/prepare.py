"""Проверка базы, импорт публикаций и установка банковского корпуса SQLite."""

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
    from app.user_store import init_app_db
    from app.db import init_db
    from app.news_store import stored_news_count
    init_app_db()
    init_db()
    print("База готова")
    print(f"новости: {stored_news_count()}")
    print("Сбор источников запускается кнопкой «Обновить данные».")
    return 0


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
            f"дублей {source['duplicates']}, пропущено по возрасту {source.get('skipped_old', 0)}, "
            f"отклонено из-за качества {source['rejected']}"
        )
    print(f"SQLite: осталось новостей {retention['remaining']}")
    return 1 if batch.status == "error" else 0


def import_bank() -> int:
    from app.bank_store import corpus_status, refresh_all_recommendations
    from app.user_store import init_app_db

    init_app_db()
    print(f"корпус банка: {corpus_status()}")
    print(f"привязки к профилям: {refresh_all_recommendations()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка базы и импорт источников")
    parser.add_argument("command", nargs="?", choices=("check", "news", "bank"), default="check")
    args = parser.parse_args(argv)
    if args.command == "news":
        return import_rss()
    if args.command == "bank":
        return import_bank()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
