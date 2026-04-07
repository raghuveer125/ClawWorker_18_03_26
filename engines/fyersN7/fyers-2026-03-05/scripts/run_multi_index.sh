#!/usr/bin/env bash
# Run multiple indices simultaneously
# Usage: scripts/run_multi_index.sh SENSEX BANKNIFTY NIFTY
# Or:    INDICES="SENSEX BANKNIFTY" scripts/run_multi_index.sh
# Default: Uses ACTIVE_INDICES from shared_project_engine/indices/config.py

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${PROJECT_ROOT:-}" ]]; then
  _resolve_project_root() {
    local current="${ROOT_DIR}"

    while [[ "${current}" != "/" ]]; do
      if [[ -d "${current}/shared_project_engine" ]]; then
        printf '%s\n' "${current}"
        return 0
      fi
      current="$(dirname "${current}")"
    done

    return 1
  }

  PROJECT_ROOT="$(_resolve_project_root || true)"
  export PROJECT_ROOT
fi

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"

if [[ "${PYTHON_BIN}" != */* ]]; then
  PYTHON_BIN="$(command -v "${PYTHON_BIN}" 2>/dev/null || true)"
fi

if [[ ! -f "${ROOT_DIR}/.fyers.env" ]]; then
  echo "Error: .fyers.env not found. Please login first." >&2
  exit 1
fi

# Get indices from args, env var, or shared config (default)
if [[ $# -gt 0 ]]; then
  INDICES=("$@")
elif [[ -n "${INDICES:-}" ]]; then
  # shellcheck disable=SC2206
  INDICES=(${INDICES})
else
  # Try to get ACTIVE_INDICES from shared_project_engine
  SHARED_INDICES=$("${PYTHON_BIN}" -c "
import sys
sys.path.insert(0, '${PROJECT_ROOT}')
try:
    from shared_project_engine.indices import ACTIVE_INDICES
    print(' '.join(ACTIVE_INDICES))
except ImportError:
    print('SENSEX NIFTY50 BANKNIFTY FINNIFTY')
" 2>/dev/null || echo "SENSEX NIFTY50 BANKNIFTY FINNIFTY")
  # shellcheck disable=SC2206
  INDICES=(${SHARED_INDICES})
fi

echo "========================================"
echo "Multi-Index Trading Engine"
echo "========================================"
echo "Indices: ${INDICES[*]}"
echo "Press Ctrl+C to stop all."
echo

PIDS=()
DONE=()
STOPPING=0

for idx in "${INDICES[@]}"; do
  echo "Starting engines for ${idx}..."
  (
    cd "${ROOT_DIR}" && \
    INDEX="${idx}" \
    INTERVAL_SEC="${INTERVAL_SEC:-15}" \
    OPP_INTERVAL_SEC="${OPP_INTERVAL_SEC:-15}" \
    scripts/run_two_engines.sh
  ) &
  PIDS+=($!)
  DONE+=(0)
  sleep 1  # Stagger startup
done

cleanup() {
  if [[ "${STOPPING}" == "1" ]]; then
    return
  fi
  STOPPING=1
  echo ""
  echo "Stopping all engines..."
  for pid in "${PIDS[@]}"; do
    kill "${pid}" 2>/dev/null || true
  done
}
trap cleanup INT TERM

# Keep remaining index launchers running even if one exits.
while true; do
  running=0
  for i in "${!PIDS[@]}"; do
    if [[ "${DONE[$i]}" == "1" ]]; then
      continue
    fi

    pid="${PIDS[$i]}"
    if kill -0 "${pid}" 2>/dev/null; then
      running=$((running + 1))
      continue
    fi

    if wait "${pid}" 2>/dev/null; then
      rc=0
    else
      rc=$?
    fi
    DONE[$i]=1

    if [[ "${rc}" -eq 0 ]]; then
      echo "[${INDICES[$i]}] launcher exited."
    else
      echo "[${INDICES[$i]}] launcher exited with code ${rc}."
    fi
  done

  if [[ "${running}" -eq 0 ]]; then
    break
  fi
  sleep 1
done
echo "All engines stopped."
