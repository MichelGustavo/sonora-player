#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv .venv

if ! .venv/bin/python -c "import PySide6, mutagen" >/dev/null 2>&1; then
    .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python app.py
