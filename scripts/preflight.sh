#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="$PYTHON_BIN"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  python_bin="$ROOT_DIR/.venv/bin/python"
else
  python_bin="$(command -v python3 || true)"
fi

if [[ -z "$python_bin" ]]; then
  printf '%s\n' "ERROR: Python 3 was not found."
  exit 127
fi

if ! command -v npm >/dev/null 2>&1; then
  printf '%s\n' "ERROR: npm was not found. Install Node.js 22 or newer."
  exit 127
fi

if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' "ERROR: Docker was not found or is not available."
  exit 127
fi

run_check() {
  local title="$1"
  shift
  printf '\n==> %s\n' "$title"
  "$@"
}

run_check "1/5 Python compile" "$python_bin" -m compileall -q app
run_check "2/5 React build" npm --prefix frontend run build
run_check "3/5 Production Docker build" docker build --quiet --file Dockerfile.render --tag finance-saas-preflight:local .
run_check "4/5 Secret scan" "$python_bin" scripts/scan_secrets.py
run_check "5/5 Configuration" "$python_bin" scripts/check_config.py

printf '\n%s\n' "OK: preflight completed successfully."
