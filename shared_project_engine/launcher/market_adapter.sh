#!/usr/bin/env bash

load_market_adapter_config() {
  local project_root="$1"
  local python_bin="${2:-python3}"
  local config

  config=$("${python_bin}" -c "
import sys
sys.path.insert(0, '${project_root}')
try:
    from shared_project_engine.services import MARKET_ADAPTER_HOST, MARKET_ADAPTER_PORT, get_market_adapter_url
    print(f'MARKET_ADAPTER_HOST={MARKET_ADAPTER_HOST}')
    print(f'MARKET_ADAPTER_PORT={MARKET_ADAPTER_PORT}')
    print(f'MARKET_ADAPTER_URL={get_market_adapter_url()}')
except ImportError:
    print('MARKET_ADAPTER_HOST=127.0.0.1')
    print('MARKET_ADAPTER_PORT=8765')
    print('MARKET_ADAPTER_URL=http://127.0.0.1:8765')
" 2>/dev/null) || true

  eval "${config}"
  MARKET_ADAPTER_HOST="${MARKET_ADAPTER_HOST:-127.0.0.1}"
  MARKET_ADAPTER_PORT="${MARKET_ADAPTER_PORT:-8765}"
  MARKET_ADAPTER_URL="${MARKET_ADAPTER_URL:-http://${MARKET_ADAPTER_HOST}:${MARKET_ADAPTER_PORT}}"
  export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL
}

market_adapter_wait_ready() {
  local service_url="$1"
  local max_wait="${2:-30}"
  local elapsed=0

  while [[ "${elapsed}" -lt "${max_wait}" ]]; do
    if curl -fsS "${service_url}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  return 1
}

ensure_market_adapter_running() {
  local project_root="$1"
  local python_bin="${2:-python3}"
  local log_file="${3:-${project_root}/logs/market_adapter.log}"
  local pid_file="${4:-}"
  local env_file="${5:-}"
  local python_path="${6:-${project_root}}"

  if [[ -z "${MARKET_ADAPTER_URL:-}" ]]; then
    load_market_adapter_config "${project_root}" "${python_bin}"
  fi

  MARKET_ADAPTER_STARTED=0
  MARKET_ADAPTER_PID=""

  if curl -fsS "${MARKET_ADAPTER_URL}/health" >/dev/null 2>&1; then
    export MARKET_ADAPTER_STARTED MARKET_ADAPTER_PID MARKET_ADAPTER_URL
    return 0
  fi

  if lsof -Pi :"${MARKET_ADAPTER_PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: port ${MARKET_ADAPTER_PORT} is already in use but ${MARKET_ADAPTER_URL}/health is not responding." >&2
    return 1
  fi

  mkdir -p "$(dirname "${log_file}")"

  local -a cmd=(
    "${python_bin}" -m shared_project_engine.market.service
    --host "${MARKET_ADAPTER_HOST}"
    --port "${MARKET_ADAPTER_PORT}"
  )
  if [[ -n "${env_file}" ]]; then
    cmd+=(--env-file "${env_file}")
  fi

  env PYTHONPATH="${python_path}:${PYTHONPATH:-}" nohup "${cmd[@]}" > "${log_file}" 2>&1 &
  MARKET_ADAPTER_PID=$!
  MARKET_ADAPTER_STARTED=1
  export MARKET_ADAPTER_STARTED MARKET_ADAPTER_PID MARKET_ADAPTER_URL

  if [[ -n "${pid_file}" ]]; then
    echo "${MARKET_ADAPTER_PID}" > "${pid_file}"
  fi

  if ! market_adapter_wait_ready "${MARKET_ADAPTER_URL}" 30; then
    echo "Error: market adapter failed to become ready at ${MARKET_ADAPTER_URL}" >&2
    return 1
  fi

  return 0
}
