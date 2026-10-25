#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Не найден python3. Установите Python 3.11+ и пакет для создания venv.' >&2
    exit 1
fi
exec python3 "$ROOT/scripts/start_linux.py" "$@"
