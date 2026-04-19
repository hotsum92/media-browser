#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
STAMP="$VENV/.deps-installed"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "warning: ffmpeg not found — video thumbnails will fail" >&2
fi

if [ ! -d "$VENV" ]; then
  echo "Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

if [ ! -f "$STAMP" ]; then
  echo "Installing dependencies"
  pip install --upgrade pip >/dev/null
  pip install Pillow
  touch "$STAMP"
fi

exec python "$DIR/media_browser.py" "$@"
