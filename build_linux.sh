#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
    python3 -m venv "$ROOT_DIR/.venv"
    PYTHON="$ROOT_DIR/.venv/bin/python"
fi

"$PYTHON" -m pip install -r requirements-build.txt
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --specpath "$ROOT_DIR/build" \
    --name SONORA \
    --collect-all PySide6.QtMultimedia \
    --collect-all PySide6.QtDBus \
    --collect-all mutagen \
    app.py

ARCH="$(uname -m)"
ARCHIVE="$ROOT_DIR/dist/SONORA-linux-$ARCH.tar.gz"
tar -czf "$ARCHIVE" -C "$ROOT_DIR/dist" SONORA

printf '\nBuild concluído.\nExecutável: %s\nPacote portátil: %s\n' \
    "$ROOT_DIR/dist/SONORA/SONORA" "$ARCHIVE"
