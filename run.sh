#!/usr/bin/env bash
# One command for a judge with a clean machine. Needs Python 3.11+.
# Node is optional: a prebuilt interface ships in frontend/dist.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
echo "CLOSER — setting up"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip -q install --upgrade pip
.venv/bin/pip -q install -r requirements-dev.txt
.venv/bin/pip -q install -e .

if [ ! -f frontend/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "Building the interface"
    (cd frontend && npm ci --silent && npm run build >/dev/null)
  else
    echo "No prebuilt interface and no npm — use '.venv/bin/closer run' for the terminal demo."
  fi
fi

echo
echo "CLOSER is starting on http://127.0.0.1:8000 — press 'Let CLOSER work'."
echo
exec .venv/bin/closer serve --host 127.0.0.1 --port 8000
