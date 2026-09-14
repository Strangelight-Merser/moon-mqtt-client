#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
if [ -x "$ROOT/.venv/bin/python" ]; then DEFAULT_PYTHON="$ROOT/.venv/bin/python"; else DEFAULT_PYTHON=python3; fi
PYTHON=${PYTHON:-"$DEFAULT_PYTHON"}
MOON=${MOON:-"$ROOT/scripts/moon.sh"}
export MOON
if [ -n "${MOSQUITTO:-}" ]; then export MOSQUITTO; fi
exec "$PYTHON" "$ROOT/tests/integration/run.py" "$@"
