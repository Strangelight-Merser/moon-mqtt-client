#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-"$ROOT/.venv/bin/python"}
MOON=${MOON:-"$ROOT/scripts/moon.sh"}
export MOON
exec "$PYTHON" "$ROOT/tests/emqx_interop.py" "$@"
