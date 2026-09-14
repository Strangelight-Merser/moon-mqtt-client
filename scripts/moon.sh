#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
if [ -z "${MOON_HOME:-}" ] && [ -x "$ROOT/.tools/moon/bin/moon" ]; then MOON_HOME="$ROOT/.tools/moon"; fi
if [ -n "${MOON_HOME:-}" ]; then export MOON_HOME; export PATH="$MOON_HOME/bin:$PATH"; fi
exec moon "$@"
