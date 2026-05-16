#!/usr/bin/env bash
# Bootstrap a dev venv (if missing) and run all linters.
# Pass `fix` as the first arg to auto-fix ruff issues.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-dev"

if [ ! -d "$VENV" ]; then
    echo "Creating dev venv at $VENV ..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$ROOT/requirements-dev.txt"
fi

export PATH="$VENV/bin:$PATH"
cd "$ROOT"

if [ "${1:-}" = "fix" ]; then
    ruff check --fix .
    ruff format .
else
    ruff check .
    ruff format --check .
fi

bandit -c pyproject.toml -r main.py
pip-audit --requirement requirements.txt --strict
