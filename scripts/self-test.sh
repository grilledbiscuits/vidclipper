#!/usr/bin/env bash
# Runs the VidClipper end-to-end test (generates a sample video, splits it,
# exports it, and verifies the output files).
set -uo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
PY="$APP_DIR/.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "The environment is not set up yet - run ./run.sh once first." >&2
    exit 1
fi

WORK="${1:-$(mktemp -d -t vidclipper-selftest-XXXXXX)}"
echo "Working directory: $WORK"
# Runs offscreen so it works over SSH and in CI.
exec env QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" \
     "$PY" "$APP_DIR/tests/e2e_test.py" "$WORK/sample.mp4" "$WORK"
