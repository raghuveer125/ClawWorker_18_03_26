#!/usr/bin/env bash
# FYERS Token Generator - One-click OAuth flow
# Usage: bash ./scripts/fyers_token.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(dirname "$ROOT_DIR")"
cd "$ROOT_DIR"

# Activate virtual environment
ACTIVATE_SCRIPT=""
for candidate in \
    "$ROOT_DIR/.venv/bin/activate" \
    "$REPO_ROOT/.venv/bin/activate" \
    "$ROOT_DIR/livebench/venv/bin/activate"
do
    if [ -f "$candidate" ]; then
        ACTIVATE_SCRIPT="$candidate"
        break
    fi
done

if [ -n "$ACTIVATE_SCRIPT" ]; then
    # shellcheck disable=SC1090
    source "$ACTIVATE_SCRIPT"
fi

# Run auto-auth script
python3 scripts/fyers_auto_auth.py
