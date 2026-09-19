#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
MOON=${MOON:-"$ROOT/scripts/moon.sh"}
if [ -x "$ROOT/.venv/bin/python" ]; then DEFAULT_PYTHON="$ROOT/.venv/bin/python"; else DEFAULT_PYTHON=python3; fi
PYTHON=${PYTHON:-"$DEFAULT_PYTHON"}
export MOON PYTHON
"$MOON" check --target native
"$MOON" test --target native
"$MOON" build --target native
./tests/integration/run.sh
"$PYTHON" tests/protocol_faults.py
"$PYTHON" tests/scenario_smoke.py
"$PYTHON" tests/consumer_smoke.py
"$PYTHON" tests/recoverable_qos1.py
