"""Offline conversion of extracted pages into a portable, account-free SQLite corpus.

This maintenance tool is not used by the service. Source PDFs stay outside the project.
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bank_store import create_corpus_schema  # noqa: E402


def build(extracted: Path, output: Path, reviewed: Path, pdf_dir: Path) -> dict:
    annotations = json.loads(reviewed.read_text(encoding="utf-8"))
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(extracted.glob("*/pages.json"))]
    if len(documents) != len(annotations):
        raise ValueError("Не все документы имеют проверенные метаданные и фрагменты")
    # Validate every input before touching the output.
    for document in documents:
        original = pdf_dir / document["file"]
        if not document["processed"] or document["errors"] or document["duplicate_of"]:
            raise ValueError(f"Не завершено извлечение: {document['id']}")
        if hashlib.sha256(original.read_bytes()).hexdigest() != document["sha256"]:
            raise ValueError(f"Изменился исходник: {document['id']}")
        if any(page["error"] for page in document["pages"]):
            raise ValueError(f"Есть ошибки страниц: {document['id']}")
        if not annotations[document["id"]]["passages"]:
            raise ValueError("Нужен хотя бы один проверенный фрагмент каждого документа")
    if output.exists():
        raise ValueError("Выберите новый выходной файл; существующий корпус не перезаписывается")
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        create_corpus_schema(connection)
        for document in documents:
            meta = annotations[document["id"]]
            first = document["pages"][0]
            connection.execute(
                "INSERT INTO bank_documents VALUES (?,?,?,?,?,?,?,?,?,?)",
                (document["id"], first["title"], meta["published_at"], meta["period"], first["topic"],
                 first["type"], document["file"], document["sha256"], len(document["pages"]), meta["notes"]),
            )
            for page in document["pages"]:
                page_id = f"{document['id']}:{page['page']}"
                normalized = re.sub(r"\s+", " ", page["text"]).strip()
                connection.execute("INSERT INTO bank_pages VALUES (?,?,?,?,?,?,?)", (
                    page_id, document["id"], page["page"], page["text"], normalized, page["kind"],
                    "raw_ocr" if len(normalized) >= 40 else "sparse",
                ))
                # Preserve every character in the page; unreviewed OCR is available in corpus search,
                # but never quoted as the bank's position in automatic recommendations.
                for number, offset in enumerate(range(0, len(normalized), 1400)):
                    text = normalized[offset:offset + 1600]
                    if len(text) >= 40:
                        connection.execute("INSERT INTO bank_passages VALUES (?,?,?,0)",
                                           (f"{page_id}:ocr:{number}", page_id, text))
            for number, passage in enumerate(meta["passages"]):
                connection.execute("INSERT INTO bank_passages VALUES (?,?,?,1)", (
                    f"{document['id']}:reviewed:{number}", f"{document['id']}:{passage['page']}", passage["text"],
                ))
        digest = hashlib.sha256()
        for table in ("bank_documents", "bank_pages", "bank_passages"):
            digest.update(json.dumps(connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall(),
                                     ensure_ascii=False).encode("utf-8"))
        connection.executemany("INSERT INTO bank_corpus_meta VALUES (?,?)", [
            ("version", digest.hexdigest()), ("format", "1"),
            ("quality_policy", "Все страницы сохранены; автоматические оценки используют только визуально сверенные фрагменты."),
        ])
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Нарушены ссылки корпуса")
        return {"documents": len(documents), "pages": connection.execute("SELECT COUNT(*) FROM bank_pages").fetchone()[0],
                "passages": connection.execute("SELECT COUNT(*) FROM bank_passages").fetchone()[0]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extracted", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, required=True, help="Визуально сверенные фрагменты и метаданные")
    parser.add_argument("--pdf-dir", type=Path, required=True, help="Внешняя папка для проверки SHA-256")
    args = parser.parse_args()
    print(build(args.extracted, args.output, args.reviewed, args.pdf_dir))
