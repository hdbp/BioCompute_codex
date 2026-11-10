#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m compileall -q biocompute
python3 -m pytest -q tests
if command -v node >/dev/null 2>&1; then
  node --check frontend/app.js
  node tests/ui_smoke.cjs
fi
printf '\nVerification complete.\n'
