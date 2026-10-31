#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
MOON=${MOON:-"$ROOT/scripts/moon.sh"}
if [ -x "$ROOT/.venv/bin/python" ]; then DEFAULT_PYTHON="$ROOT/.venv/bin/python"; else DEFAULT_PYTHON=python3; fi
PYTHON=${PYTHON:-"$DEFAULT_PYTHON"}
export MOON PYTHON
exec "$PYTHON" scripts/acceptance.py "$@"
