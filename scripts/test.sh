#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

command -v python3 >/dev/null 2>&1 || { echo "Python 3.11+ не найден." >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm не найден. Установите Node.js LTS." >&2; exit 1; }

echo "== Подготовка Python-зависимостей =="
if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt

echo
echo "== Установка frontend-зависимостей =="
(cd ui && npm ci)

echo
echo "== Python-тесты =="
.venv/bin/python -m pytest -v

echo
echo "== Проверка TypeScript и сборка UI =="
(cd ui && npm run build)

echo
echo "Все проверки завершились успешно."
