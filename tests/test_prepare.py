import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_prepare_check_reports_the_demo_set():
    result = subprocess.run(
        [sys.executable, "scripts/prepare.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0
    assert "демо-набор на месте" in result.stdout
    assert "новости:" in result.stdout
    assert "sk-" not in result.stdout


def test_prepare_bank_stops_without_pdf_folder(tmp_path):
    env = os.environ.copy()
    env["BANK_PDF_DIR"] = str(tmp_path / "missing-pdfs")
    env["PROFILE_SOURCE"] = "demo"
    result = subprocess.run(
        [sys.executable, "scripts/prepare.py", "bank"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )
    assert result.returncode == 1
    assert "папка PDF не найдена" in result.stdout
