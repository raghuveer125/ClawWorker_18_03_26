#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
REPORT_SCRIPT="${ROOT_DIR}/scripts/show_paper_report.py"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Error: ${PYTHON_BIN} not found. Create venv first." >&2
  exit 1
fi

"${PYTHON_BIN}" "${REPORT_SCRIPT}" "$@"
