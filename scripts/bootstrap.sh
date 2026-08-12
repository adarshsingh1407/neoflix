#!/usr/bin/env bash
# Runs the one-time post-setup automation (see SETUP.md step 6 and
# DESIGN_DECISIONS.md decision #13). Safe to re-run — every step checks
# existing state first.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "Missing .env — run 'cp .env.example .env' and fill it in first." >&2
  exit 1
fi

if [[ ! -f credentials.env ]]; then
  echo "Missing credentials.env — run 'cp credentials.env.example credentials.env' and fill it in first." >&2
  exit 1
fi

VENV_DIR=".bootstrap-venv"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r scripts/requirements.txt

"$VENV_DIR/bin/python" scripts/bootstrap.py
