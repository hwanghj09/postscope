#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if command -v uv >/dev/null 2>&1; then
  uv sync --frozen --no-dev
  exec uv run --no-sync python -m postscope "$@"
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --require-hashes -r requirements.txt
exec .venv/bin/python -m postscope "$@"
