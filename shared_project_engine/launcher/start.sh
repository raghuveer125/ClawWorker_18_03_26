#!/usr/bin/env bash
#
# Unified Launcher for ClawWork + fyersN7
# ========================================
#
# Usage:
#   ./start.sh                    # Show menu
#   ./start.sh login              # FYERS login
#   ./start.sh status             # Check auth status
#   ./start.sh forensics-check    # Validate daily forensics inputs
#   ./start.sh quality-check      # Run forensics quality gate
#   ./start.sh timeline-build     # Build canonical forensics timeline
#   ./start.sh regime-detect      # Generate regime_table and turning_points
#   ./start.sh trigger-signals    # Generate trigger_signals from turning points
#   ./start.sh pattern-templates  # Generate pattern_templates from trigger history
#   ./start.sh run-summary        # Generate daily run_summary markdown/json
#   ./start.sh canary-metrics     # Generate canary_metrics rollback inputs
#   ./start.sh bot-rules-update   # Generate bot_rules_update json proposals
#   ./start.sh market-report      # Show shared market-adapter duplicate metrics
#   ./start.sh clawwork           # Start ClawWork paper trading
#   ./start.sh fyersn7            # Start fyersN7 signal engine
#   ./start.sh fyersn7-paper      # Start fyersN7 paper trading
#   ./start.sh fyersn7-live       # Start fyersN7 live trading (optimized 69.7% WR)
#   ./start.sh both               # Start both systems
#   ./start.sh all                # Start ALL engines (both + scalping + llm-debate)
#   ./start.sh scalping           # Start 18-agent scalping system only
#   ./start.sh llm-debate         # Start LLM debate backend only
#   ./start.sh stop               # Stop all running processes
#
set -euo pipefail

# Resolve directories (handle symlinks properly)
SCRIPT_PATH="${BASH_SOURCE[0]}"
# Follow symlinks to get real path
while [[ -L "${SCRIPT_PATH}" ]]; do
  SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
  SCRIPT_PATH="$(readlink "${SCRIPT_PATH}")"
  [[ "${SCRIPT_PATH}" != /* ]] && SCRIPT_PATH="${SCRIPT_DIR}/${SCRIPT_PATH}"
done
SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLAWWORK_DIR="${PROJECT_ROOT}/ClawWork"
FYERSN7_DIR="${PROJECT_ROOT}/fyersN7/fyers-2026-03-05"
SHARED_ENGINE="${PROJECT_ROOT}/shared_project_engine"
BOT_ARMY_DIR="${PROJECT_ROOT}/bot_army"
LLM_DEBATE_DIR="${PROJECT_ROOT}/llm_debate"
MARKET_ADAPTER_LOG="${PROJECT_ROOT}/logs/market_adapter.log"
MARKET_ADAPTER_PID_FILE="${PROJECT_ROOT}/logs/market_adapter.pid"
MARKET_ADAPTER_HELPER="${SHARED_ENGINE}/launcher/market_adapter.sh"
AUTH_ENV_FILE="${PROJECT_ROOT}/.env"

# Python - prefer an explicit override, then repo-managed virtualenvs, then PATH.
resolve_python_bin() {
  local candidates=(
    "${PROJECT_ROOT}/.venv/bin/python"
    "${CLAWWORK_DIR}/livebench/venv/bin/python"
  )
  local candidate

  for candidate in "${candidates[@]}"; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi

  printf '%s\n' "/usr/bin/python3"
}

PYTHON_BIN="${PYTHON_BIN:-$(resolve_python_bin)}"

# Set PYTHONPATH so shared_project_engine can be found
export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

if [[ -z "${DETACH_AFTER_START:-}" ]]; then
  if [[ -t 1 ]]; then
    DETACH_AFTER_START=0
  else
    DETACH_AFTER_START=1
  fi
fi

# PID file for tracking processes
PID_FILE="${PROJECT_ROOT}/.running_pids"

# ============================================================================
# Shared Config (from shared_project_engine.services)
# ============================================================================
# Load defaults from Python config, with env overrides
_load_shared_config() {
  local config
  config=$("${PYTHON_BIN}" -c "
import sys
sys.path.insert(0, '${PROJECT_ROOT}')
try:
    from shared_project_engine.services import (
        API_PORT,
        FRONTEND_PORT,
        AUTH_CALLBACK_PORT,
        MARKET_ADAPTER_HOST,
        MARKET_ADAPTER_PORT,
        get_auth_redirect_uri,
        get_market_adapter_url,
    )
    print(f'API_PORT={API_PORT}')
    print(f'FRONTEND_PORT={FRONTEND_PORT}')
    print(f'AUTH_CALLBACK_PORT={AUTH_CALLBACK_PORT}')
    print(f'MARKET_ADAPTER_HOST={MARKET_ADAPTER_HOST}')
    print(f'MARKET_ADAPTER_PORT={MARKET_ADAPTER_PORT}')
    print(f'MARKET_ADAPTER_URL={get_market_adapter_url()}')
    print(f'DEFAULT_REDIRECT_URI={get_auth_redirect_uri()}')
except ImportError:
    print('API_PORT=8001')
    print('FRONTEND_PORT=3001')
    print('AUTH_CALLBACK_PORT=8080')
    print('MARKET_ADAPTER_HOST=127.0.0.1')
    print('MARKET_ADAPTER_PORT=8765')
    print('MARKET_ADAPTER_URL=http://127.0.0.1:8765')
    print('DEFAULT_REDIRECT_URI=http://127.0.0.1:8080/')
" 2>/dev/null) || true
  eval "${config}"
}
_load_shared_config

# Apply env overrides (env vars take precedence)
API_PORT="${API_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-3001}"
AUTH_CALLBACK_PORT="${AUTH_CALLBACK_PORT:-8080}"
MARKET_ADAPTER_HOST="${MARKET_ADAPTER_HOST:-127.0.0.1}"
MARKET_ADAPTER_PORT="${MARKET_ADAPTER_PORT:-8765}"
MARKET_ADAPTER_URL="${MARKET_ADAPTER_URL:-http://${MARKET_ADAPTER_HOST}:${MARKET_ADAPTER_PORT}}"
MARKET_ADAPTER_STRICT="${MARKET_ADAPTER_STRICT:-1}"
DEFAULT_REDIRECT_URI="${FYERS_REDIRECT_URI:-${DEFAULT_REDIRECT_URI:-http://127.0.0.1:${AUTH_CALLBACK_PORT}/}}"
export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL MARKET_ADAPTER_STRICT

if [[ -f "${MARKET_ADAPTER_HELPER}" ]]; then
  # shellcheck source=/dev/null
  source "${MARKET_ADAPTER_HELPER}"
fi

# ============================================================================
# Helper Functions
# ============================================================================

ensure_scalping_cron() {
  if ! command -v crontab >/dev/null 2>&1; then
    echo -e "${YELLOW}Warning: crontab not available; skipping scalping scheduler setup.${NC}"
    return 0
  fi

  mkdir -p "${PROJECT_ROOT}/logs" 2>/dev/null || true

  # Start at 8:58 AM, Mon-Fri (market hours: 8:58 AM - 3:40 PM IST)
  local cron_schedule="58 8 * * 1-5"
  local cron_marker_begin="# >>> ClawWork scalping-engine >>>"
  local cron_marker_end="# <<< ClawWork scalping-engine <<<"
  local cron_command="cd \"${PROJECT_ROOT}\" && ./start.sh all >> \"${PROJECT_ROOT}/logs/startup.log\" 2>&1"
  local desired_cron_line="${cron_schedule} ${cron_command}"
  local current_crontab
  local filtered_crontab
  local desired_count
  local marker_begin_count
  local marker_end_count

  current_crontab="$(crontab -l 2>/dev/null || true)"

  desired_count="$(
    printf '%s\n' "${current_crontab}" | grep -Fxc -- "${desired_cron_line}" 2>/dev/null || true
  )"
  marker_begin_count="$(
    printf '%s\n' "${current_crontab}" | grep -Fxc -- "${cron_marker_begin}" 2>/dev/null || true
  )"
  marker_end_count="$(
    printf '%s\n' "${current_crontab}" | grep -Fxc -- "${cron_marker_end}" 2>/dev/null || true
  )"

  if [[ "${desired_count}" -eq 1 && "${marker_begin_count}" -eq 1 && "${marker_end_count}" -eq 1 ]]; then
    echo -e "${GREEN}Weekday scalping cron already set at ${cron_schedule}; skipping.${NC}"
    return 0
  fi

  filtered_crontab="$(
    printf '%s\n' "${current_crontab}" | awk \
      -v begin="${cron_marker_begin}" \
      -v end="${cron_marker_end}" \
      -v desired="${desired_cron_line}" '
      $0 == begin { skip=1; next }
      $0 == end { skip=0; next }
      $0 == desired { next }
      skip != 1 { print }
    '
  )"

  if ! {
    if [[ -n "${filtered_crontab}" ]]; then
      printf '%s\n' "${filtered_crontab}"
    fi
    printf '%s\n' "${cron_marker_begin}"
    printf '%s\n' "${desired_cron_line}"
    printf '%s\n' "${cron_marker_end}"
  } | crontab -; then
    echo -e "${YELLOW}Warning: failed to install scalping scheduler; continuing without it.${NC}"
    return 0
  fi

  echo -e "${GREEN}Installed weekday scalping cron at ${cron_schedule} (8:58 AM Mon-Fri).${NC}"
}

ensure_bot_rules_cron() {
  if ! command -v crontab >/dev/null 2>&1; then
    echo -e "${YELLOW}Warning: crontab not available; skipping bot-rules scheduler setup.${NC}"
    return 0
  fi

  mkdir -p "${PROJECT_ROOT}/logs" 2>/dev/null || true

  local cron_schedule="${BOT_RULES_CRON_SCHEDULE:-45 15 * * 1-5}"
  local cron_marker_begin="# >>> ClawWork bot-rules-update >>>"
  local cron_marker_end="# <<< ClawWork bot-rules-update <<<"
  local cron_command="cd \"${PROJECT_ROOT}\" && ./start.sh bot-rules-update >> \"${PROJECT_ROOT}/logs/cron_bot_rules.log\" 2>&1"
  local legacy_cron_command="cd ${PROJECT_ROOT} && ./start.sh bot-rules-update >> ${PROJECT_ROOT}/logs/cron_bot_rules.log 2>&1"
  local desired_cron_line="${cron_schedule} ${cron_command}"
  local legacy_cron_line="${cron_schedule} ${legacy_cron_command}"
  local current_crontab
  local filtered_crontab
  local desired_count
  local legacy_count
  local marker_begin_count
  local marker_end_count
  local match_total

  current_crontab="$(crontab -l 2>/dev/null || true)"

  desired_count="$(
    printf '%s\n' "${current_crontab}" | grep -Fxc -- "${desired_cron_line}" 2>/dev/null || true
  )"
  legacy_count="$(
    printf '%s\n' "${current_crontab}" | grep -Fxc -- "${legacy_cron_line}" 2>/dev/null || true
  )"
  marker_begin_count="$(
    printf '%s\n' "${current_crontab}" | grep -Fxc -- "${cron_marker_begin}" 2>/dev/null || true
  )"
  marker_end_count="$(
    printf '%s\n' "${current_crontab}" | grep -Fxc -- "${cron_marker_end}" 2>/dev/null || true
  )"
  match_total=$(( desired_count + legacy_count ))

  if [[ "${desired_count}" -eq 1 && "${match_total}" -eq 1 && "${marker_begin_count}" -eq 1 && "${marker_end_count}" -eq 1 ]]; then
    echo -e "${GREEN}Weekday bot-rules-update cron already set at ${cron_schedule}; skipping.${NC}"
    return 0
  fi

  if [[ "${legacy_count}" -eq 1 && "${match_total}" -eq 1 && "${marker_begin_count}" -eq 0 && "${marker_end_count}" -eq 0 ]]; then
    echo -e "${GREEN}Weekday bot-rules-update cron already present at ${cron_schedule}; skipping.${NC}"
    return 0
  fi

  filtered_crontab="$(
    printf '%s\n' "${current_crontab}" | awk \
      -v begin="${cron_marker_begin}" \
      -v end="${cron_marker_end}" \
      -v desired="${desired_cron_line}" \
      -v legacy="${legacy_cron_line}" '
      $0 == begin { skip=1; next }
      $0 == end { skip=0; next }
      $0 == desired { next }
      $0 == legacy { next }
      skip != 1 { print }
    '
  )"

  if ! {
    if [[ -n "${filtered_crontab}" ]]; then
      printf '%s\n' "${filtered_crontab}"
    fi
    printf '%s\n' "${cron_marker_begin}"
    printf '%s\n' "${desired_cron_line}"
    printf '%s\n' "${cron_marker_end}"
  } | crontab -; then
    echo -e "${YELLOW}Warning: failed to install bot-rules scheduler; continuing without it.${NC}"
    return 0
  fi

  echo -e "${GREEN}Installed weekday bot-rules-update cron at ${cron_schedule}.${NC}"
}

print_banner() {
  echo -e "${CYAN}"
  echo "╔═══════════════════════════════════════════════════════════════╗"
  echo "║     ClawWork + fyersN7 Unified Launcher                       ║"
  echo "║     Shared Project Engine v1.0                                ║"
  echo "╚═══════════════════════════════════════════════════════════════╝"
  echo -e "${NC}"
}

print_menu() {
  echo -e "${BLUE}Available Commands:${NC}"
  echo ""
  echo -e "  ${GREEN}login${NC}           - FYERS OAuth login (opens browser)"
  echo -e "  ${GREEN}status${NC}          - Check authentication status"
  echo -e "  ${GREEN}test${NC}            - Test API connection"
  echo -e "  ${GREEN}forensics-check${NC} - Validate latest postmortem inputs (SENSEX,NIFTY50)"
  echo -e "  ${GREEN}quality-check${NC}   - Run quality gate on latest postmortem data"
  echo -e "  ${GREEN}timeline-build${NC}  - Build canonical timeline from decision+signals"
  echo -e "  ${GREEN}regime-detect${NC}   - Build regime_table and turning_points outputs"
  echo -e "  ${GREEN}trigger-signals${NC} - Build trigger_signals from regime + turning points"
  echo -e "  ${GREEN}pattern-templates${NC} - Build pattern_templates from trigger history"
  echo -e "  ${GREEN}run-summary${NC}    - Build daily run_summary markdown/json"
  echo -e "  ${GREEN}canary-metrics${NC} - Build canary_metrics rollback inputs"
  echo -e "  ${GREEN}bot-rules-update${NC} - Build bot_rules_update proposals"
  echo -e "  ${GREEN}market-report${NC}  - Show shared market-adapter duplicate metrics"
  echo -e "  ${GREEN}market-audit${NC}   - Audit strict adapter mode and MarketDataClient call sites"
  echo ""
  echo -e "  ${YELLOW}both${NC}            - Start everything (signals + dashboard + paper trading)"
  echo -e "  ${YELLOW}all${NC}             - Start ALL engines (both + scalping + llm-debate)"
  echo -e "  ${YELLOW}scalping${NC}        - Start 18-agent scalping system (8:58 AM - 3:40 PM)"
  echo -e "  ${YELLOW}llm-debate${NC}      - Start LLM debate backend server"
  echo -e "  ${YELLOW}dashboard${NC}       - Start dashboard only (API + frontend)"
  echo -e "  ${YELLOW}fyersn7${NC}         - Start fyersN7 signal engine only"
  echo ""
  echo -e "  ${RED}stop${NC}            - Stop all running processes"
  echo -e "  ${RED}logs${NC}            - View combined logs"
  echo ""
  echo -e "${BLUE}Examples:${NC}"
  echo "  ./start.sh both              # Full system"
  echo "  ./start.sh dashboard         # Dashboard only"
  echo "  INDEX=BANKNIFTY ./start.sh fyersn7"
  echo ""
}

check_python() {
  if [[ "${PYTHON_BIN}" == */* ]]; then
    if [[ -x "${PYTHON_BIN}" ]]; then
      return 0
    fi
  elif command -v "${PYTHON_BIN}" &>/dev/null; then
    return 0
  fi

  echo -e "${RED}Error: ${PYTHON_BIN} not found${NC}" >&2
  exit 1
}

check_auth_python_deps() {
  local missing=()
  local module

  for module in requests urllib3; do
    if ! "${PYTHON_BIN}" -c "import ${module}" >/dev/null 2>&1; then
      missing+=("${module}")
    fi
  done

  if [[ "${#missing[@]}" -eq 0 ]]; then
    return 0
  fi

  echo -e "${RED}Error: missing Python packages for authentication: ${missing[*]}${NC}" >&2
  echo -e "${YELLOW}Create a repo-local venv and install them with:${NC}" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  .venv/bin/pip install requests urllib3" >&2
  exit 1
}

check_auth_ready() {
  check_python
  check_auth_python_deps
}

is_truthy() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

run_auth_cli() {
  local subcommand="$1"
  shift || true

  local insecure_enabled=0
  if is_truthy "${FYERS_INSECURE:-${FYERS_AUTH_INSECURE:-}}"; then
    insecure_enabled=1
  fi

  local ca_bundle="${FYERS_CA_BUNDLE:-${REQUESTS_CA_BUNDLE:-${SSL_CERT_FILE:-}}}"

  if [[ "${insecure_enabled}" == "1" && -n "${ca_bundle}" ]]; then
    "${PYTHON_BIN}" -m shared_project_engine.auth.cli --env-file "${AUTH_ENV_FILE}" "${subcommand}" --insecure --ca-bundle "${ca_bundle}" "$@"
  elif [[ "${insecure_enabled}" == "1" ]]; then
    "${PYTHON_BIN}" -m shared_project_engine.auth.cli --env-file "${AUTH_ENV_FILE}" "${subcommand}" --insecure "$@"
  elif [[ -n "${ca_bundle}" ]]; then
    "${PYTHON_BIN}" -m shared_project_engine.auth.cli --env-file "${AUTH_ENV_FILE}" "${subcommand}" --ca-bundle "${ca_bundle}" "$@"
  else
    "${PYTHON_BIN}" -m shared_project_engine.auth.cli --env-file "${AUTH_ENV_FILE}" "${subcommand}" "$@"
  fi
}

run_auth_status() {
  run_auth_cli status
}

print_auth_tls_hint() {
  echo -e "${YELLOW}Hint: if your network uses a self-signed or corporate TLS certificate, set FYERS_INSECURE=1 or FYERS_CA_BUNDLE=/path/to/corp-ca.pem in .env and retry.${NC}"
}

check_frontend_runtime() {
  if ! command -v node >/dev/null 2>&1; then
    echo -e "${RED}Error: node is not installed or not on PATH.${NC}" >&2
    return 1
  fi

  if ! command -v npm >/dev/null 2>&1; then
    echo -e "${RED}Error: npm is not installed or not on PATH.${NC}" >&2
    return 1
  fi

  local node_output
  if ! node_output="$(node --version 2>&1)"; then
    echo -e "${RED}Error: Node.js runtime is not healthy.${NC}" >&2
    if echo "${node_output}" | grep -qi "libsimdjson"; then
      echo -e "${YELLOW}Homebrew node is linked against a missing simdjson library. Repair it with:${NC}" >&2
      echo "  brew reinstall simdjson node" >&2
    else
      printf '%s\n' "${node_output}" >&2
    fi
    return 1
  fi

  local npm_output
  if ! npm_output="$(npm --version 2>&1)"; then
    echo -e "${RED}Error: npm is not healthy.${NC}" >&2
    if echo "${npm_output}" | grep -qi "libsimdjson"; then
      echo -e "${YELLOW}Homebrew node/npm is linked against a missing simdjson library. Repair it with:${NC}" >&2
      echo "  brew reinstall simdjson node" >&2
    else
      printf '%s\n' "${npm_output}" >&2
    fi
    return 1
  fi
}

load_env() {
  local env_file="${PROJECT_ROOT}/.env"
  if [[ -f "${env_file}" ]]; then
    set -a
    source "${env_file}"
    set +a
    return 0
  fi
  return 1
}

# Verify auth and prompt for login if needed
ensure_auth() {
  load_env || true

  # Check if we have credentials
  if [[ -z "${FYERS_ACCESS_TOKEN:-}" ]] || [[ -z "${FYERS_CLIENT_ID:-}" ]]; then
    echo -e "${YELLOW}No valid credentials found. Starting login...${NC}"
    if ! cmd_login; then
      print_auth_tls_hint
      return 1
    fi
    # Reload env after login
    load_env || true
  fi

  # Verify token is valid
  echo -e "${BLUE}Verifying authentication...${NC}"
  local status_output
  status_output="$(run_auth_status 2>&1)" || true

  if echo "${status_output}" | grep -q "Token is VALID"; then
    echo -e "${GREEN}Authentication valid.${NC}"
    return 0
  elif echo "${status_output}" | grep -q "expired\|INVALID"; then
    echo -e "${YELLOW}Token expired. Starting login...${NC}"
    if ! cmd_login; then
      print_auth_tls_hint
      return 1
    fi
    load_env || true
    echo -e "${BLUE}Re-verifying authentication...${NC}"
    status_output="$(run_auth_status 2>&1)" || true
    if echo "${status_output}" | grep -q "Token is VALID"; then
      echo -e "${GREEN}Authentication valid.${NC}"
      return 0
    fi
    echo -e "${RED}Authentication refresh failed. Please run: ./start.sh login${NC}"
    if echo "${status_output}" | grep -qi "CERTIFICATE_VERIFY_FAILED\|self-signed certificate"; then
      print_auth_tls_hint
    fi
    return 1
  else
    echo -e "${RED}Authentication check failed. Please run: ./start.sh login${NC}"
    if echo "${status_output}" | grep -qi "CERTIFICATE_VERIFY_FAILED\|self-signed certificate"; then
      print_auth_tls_hint
    fi
    return 1
  fi
}

save_pid() {
  local name="$1"
  local pid="$2"
  mkdir -p "$(dirname "${PID_FILE}")"
  touch "${PID_FILE}"
  if [[ -f "${PID_FILE}" ]] && grep -Fqx "${name}:${pid}" "${PID_FILE}" 2>/dev/null; then
    return 0
  fi
  echo "${name}:${pid}" >> "${PID_FILE}"
}

should_detach() {
  [[ "${DETACH_AFTER_START}" == "1" ]]
}

register_fyersn7_workers() {
  local pid_file pid index lock_dir lock_name registered=0

  if [[ ! -d "${FYERSN7_DIR}/postmortem" ]]; then
    return 0
  fi

  while IFS= read -r pid_file; do
    pid="$(tr -dc '0-9' < "${pid_file}" 2>/dev/null || true)"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
      continue
    fi

    lock_dir="$(basename "$(dirname "${pid_file}")")"
    lock_name="${lock_dir#.}"
    lock_name="${lock_name%.lock}"
    index="$(basename "$(dirname "$(dirname "${pid_file}")")")"
    save_pid "fyersn7-${index}-${lock_name}" "${pid}"
    registered=$((registered + 1))
  done < <(find "${FYERSN7_DIR}/postmortem" -type f -path '*/.*.lock/pid' 2>/dev/null | sort)

  if [[ "${registered}" -gt 0 ]]; then
    echo -e "${GREEN}  Registered ${registered} fyersN7 worker processes.${NC}"
  fi
}

stop_fyersn7_workers() {
  local pid_file pid index lock_dir lock_name

  if [[ ! -d "${FYERSN7_DIR}/postmortem" ]]; then
    return 0
  fi

  while IFS= read -r pid_file; do
    pid="$(tr -dc '0-9' < "${pid_file}" 2>/dev/null || true)"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
      continue
    fi

    lock_dir="$(basename "$(dirname "${pid_file}")")"
    lock_name="${lock_dir#.}"
    lock_name="${lock_name%.lock}"
    index="$(basename "$(dirname "$(dirname "${pid_file}")")")"
    echo -e "  Stopping fyersN7 ${index} ${lock_name} (PID: ${pid})..."
    kill "${pid}" 2>/dev/null || true
  done < <(find "${FYERSN7_DIR}/postmortem" -type f -path '*/.*.lock/pid' 2>/dev/null | sort)
}

ensure_frontend_dependencies() {
  local frontend_dir="${CLAWWORK_DIR}/frontend"

  check_frontend_runtime || return 1

  if [[ -x "${frontend_dir}/node_modules/.bin/vite" ]]; then
    return 0
  fi

  echo -e "${YELLOW}Frontend dev dependencies missing or incomplete; running npm install --include=dev...${NC}"
  (
    cd "${frontend_dir}"
    npm install --include=dev
  )
}

resolve_service_python() {
  local candidate

  for candidate in "$@"; do
    [[ -n "${candidate}" ]] || continue

    if [[ "${candidate}" == */* ]]; then
      if [[ -x "${candidate}" ]]; then
        printf '%s\n' "${candidate}"
        return 0
      fi
      continue
    fi

    if command -v "${candidate}" >/dev/null 2>&1; then
      command -v "${candidate}"
      return 0
    fi
  done

  return 1
}

livebench_python_bin() {
  resolve_service_python \
    "${CLAWWORK_DIR}/livebench/venv/bin/python" \
    "${PYTHON_BIN}" \
    "${PROJECT_ROOT}/.venv/bin/python" \
    "python3" \
    "/usr/bin/python3"
}

llm_debate_python_bin() {
  resolve_service_python \
    "${LLM_DEBATE_DIR}/backend/venv/bin/python" \
    "${PYTHON_BIN}" \
    "${PROJECT_ROOT}/.venv/bin/python" \
    "python3" \
    "/usr/bin/python3"
}

start_fyers_screener_loop() {
  local step_label="${1:-}"
  local screener_script="${CLAWWORK_DIR}/scripts/fyers_screener.sh"
  local screener_log="${CLAWWORK_DIR}/logs/fyers_screener_loop.log"

  if [[ ! -f "${screener_script}" ]]; then
    echo -e "${YELLOW}  Fyers Screener script not found at ${screener_script}, skipping...${NC}"
    return 1
  fi

  SCREENER_INTERVAL="${SCREENER_INTERVAL:-15}"
  echo -e "${BLUE}${step_label} Starting Fyers Screener Loop (${SCREENER_INTERVAL}s interval)...${NC}"
  nohup bash -lc "cd '${CLAWWORK_DIR}' && while true; do echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] Running FYERS screener\"; bash './scripts/fyers_screener.sh'; status=\$?; if [[ \$status -ne 0 ]]; then echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] FYERS screener exited with status \$status\"; fi; sleep '${SCREENER_INTERVAL}'; done" >> "${screener_log}" 2>&1 &
  SCREENER_PID=$!
  save_pid "fyers_screener" "${SCREENER_PID}"
  echo -e "${GREEN}  Fyers Screener started (PID: ${SCREENER_PID})${NC}"
  echo -e "${GREEN}  Screener log: ${screener_log}${NC}"
}

monitor_processes() {
  trap stop_all SIGINT SIGTERM

  while true; do
    sleep 5
    if [[ -f "${PID_FILE}" ]]; then
      local live_entries=()
      while IFS=: read -r name pid; do
        [[ -n "${name}" && -n "${pid}" ]] || continue
        if ! kill -0 "${pid}" 2>/dev/null; then
          echo -e "${RED}Process ${name} (PID: ${pid}) died. Check logs.${NC}"
          continue
        fi
        live_entries+=("${name}:${pid}")
      done < "${PID_FILE}"
      : > "${PID_FILE}"
      if [[ "${#live_entries[@]}" -gt 0 ]]; then
        printf '%s\n' "${live_entries[@]}" > "${PID_FILE}"
      fi
    fi
  done
}

ensure_market_adapter() {
  local env_file="${PROJECT_ROOT}/.env"
  if [[ ! -f "${env_file}" ]]; then
    env_file=""
  fi

  if [[ -f "${MARKET_ADAPTER_HELPER}" ]]; then
    load_market_adapter_config "${PROJECT_ROOT}" "${PYTHON_BIN}"
  fi

  if ! ensure_market_adapter_running "${PROJECT_ROOT}" "${PYTHON_BIN}" "${MARKET_ADAPTER_LOG}" "${MARKET_ADAPTER_PID_FILE}" "${env_file}" "${PROJECT_ROOT}"; then
    echo -e "${RED}Failed to start shared market adapter.${NC}" >&2
    exit 1
  fi

  if [[ "${MARKET_ADAPTER_STARTED:-0}" == "1" ]] && [[ -n "${MARKET_ADAPTER_PID:-}" ]]; then
    save_pid "market_adapter" "${MARKET_ADAPTER_PID}"
    echo -e "${GREEN}Market adapter started at ${MARKET_ADAPTER_URL} (PID: ${MARKET_ADAPTER_PID}).${NC}"
  else
    echo -e "${GREEN}Using existing market adapter at ${MARKET_ADAPTER_URL}.${NC}"
  fi
}

stop_all() {
  echo -e "${YELLOW}Stopping all processes...${NC}"

  if [[ -f "${PID_FILE}" ]]; then
    while IFS=: read -r name pid; do
      if kill -0 "${pid}" 2>/dev/null; then
        echo -e "  Stopping ${name} (PID: ${pid})..."
        kill "${pid}" 2>/dev/null || true
      fi
    done < "${PID_FILE}"
    rm -f "${PID_FILE}"
  fi

  stop_fyersn7_workers

  # Kill by process name (ensure thorough cleanup)
  pkill -f "uvicorn scalping.api" 2>/dev/null || true
  pkill -f "paper_trade_loop" 2>/dev/null || true
  pkill -f "llm_debate" 2>/dev/null || true
  pkill -f "fyers_screener" 2>/dev/null || true

  # Also kill by port (fallback) - include scalping API (8002) and LLM debate (8080)
  for port in "${API_PORT}" "${FRONTEND_PORT}" "${MARKET_ADAPTER_PORT}" 8787 8002 8080; do
    lsof -ti :"${port}" 2>/dev/null | xargs kill -9 2>/dev/null || true
  done

  # Small delay to ensure ports are released
  sleep 1

  echo -e "${GREEN}All processes stopped.${NC}"
}

# ============================================================================
# Auth Commands (uses shared_project_engine.auth)
# ============================================================================

cmd_login() {
  check_auth_ready
  load_env || true
  cd "${PROJECT_ROOT}"
  echo -e "${BLUE}Starting FYERS login...${NC}"
  run_auth_cli login "$@"
}

cmd_status() {
  check_auth_ready
  load_env || true
  cd "${PROJECT_ROOT}"
  run_auth_cli status "$@"
}

cmd_test() {
  check_auth_ready
  load_env || true
  cd "${PROJECT_ROOT}"
  run_auth_cli test "$@"
}

# ============================================================================
# ClawWork Commands
# ============================================================================

cmd_clawwork() {
  check_python
  load_env || true

  if [[ ! -d "${CLAWWORK_DIR}" ]]; then
    echo -e "${RED}Error: ClawWork directory not found at ${CLAWWORK_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Starting ClawWork Paper Trading...${NC}"
  echo -e "  API Port: ${API_PORT:-8001}"
  echo -e "  Frontend Port: ${FRONTEND_PORT:-3001}"
  echo ""

  cd "${CLAWWORK_DIR}"

  # Use existing start script
  if [[ -x "./start_paper_trading.sh" ]]; then
    exec ./start_paper_trading.sh
  else
    echo -e "${RED}Error: start_paper_trading.sh not found${NC}" >&2
    exit 1
  fi
}

# ============================================================================
# fyersN7 Commands
# ============================================================================

cmd_fyersn7() {
  check_python

  # Verify auth first (will trigger login if needed)
  ensure_auth || exit 1

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  # Copy shared .env to fyersN7 .fyers.env format
  if [[ -f "${PROJECT_ROOT}/.env" ]] && [[ -n "${FYERS_ACCESS_TOKEN:-}" ]]; then
    cat > "${FYERSN7_DIR}/.fyers.env" << EOF
# FYERS local credentials (synced from shared .env)
FYERS_CLIENT_ID=${FYERS_CLIENT_ID:-}
FYERS_SECRET_KEY=${FYERS_SECRET_KEY:-}
FYERS_REDIRECT_URI=${DEFAULT_REDIRECT_URI}
FYERS_ACCESS_TOKEN=${FYERS_ACCESS_TOKEN:-}
EOF
    echo -e "${GREEN}Synced credentials to fyersN7/.fyers.env${NC}"
  fi

  local index="${INDEX:-SENSEX}"
  echo -e "${GREEN}Starting fyersN7 Signal Engine...${NC}"
  echo -e "  Index: ${index}"
  echo -e "  Interval: ${INTERVAL_SEC:-15}s"
  echo ""

  cd "${FYERSN7_DIR}"

  # Use existing start script
  if [[ -x "./scripts/start_all.sh" ]]; then
    ensure_market_adapter
    PYTHON_BIN="${PYTHON_BIN}" INDEX="${index}" MARKET_ADAPTER_URL="${MARKET_ADAPTER_URL}" exec ./scripts/start_all.sh run
  else
    echo -e "${RED}Error: scripts/start_all.sh not found${NC}" >&2
    exit 1
  fi
}

cmd_fyersn7_paper() {
  check_python

  # Verify auth first (will trigger login if needed)
  ensure_auth || exit 1

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found${NC}" >&2
    exit 1
  fi

  # Sync credentials
  if [[ -f "${PROJECT_ROOT}/.env" ]] && [[ -n "${FYERS_ACCESS_TOKEN:-}" ]]; then
    cat > "${FYERSN7_DIR}/.fyers.env" << EOF
FYERS_CLIENT_ID=${FYERS_CLIENT_ID:-}
FYERS_SECRET_KEY=${FYERS_SECRET_KEY:-}
FYERS_REDIRECT_URI=${DEFAULT_REDIRECT_URI}
FYERS_ACCESS_TOKEN=${FYERS_ACCESS_TOKEN:-}
EOF
  fi

  local index="${INDEX:-SENSEX}"
  echo -e "${GREEN}Starting fyersN7 Paper Trading...${NC}"
  echo -e "  Index: ${index}"
  echo -e "  Capital: ${CAPITAL:-5000}"
  echo ""

  cd "${FYERSN7_DIR}"
  ensure_market_adapter
  PYTHON_BIN="${PYTHON_BIN}" INDEX="${index}" MARKET_ADAPTER_URL="${MARKET_ADAPTER_URL}" exec ./scripts/start_all.sh paper
}

cmd_fyersn7_live() {
  # FyersN7 Live Trading with optimized Hub V2 settings
  # Uses: 25/15 point exit, REQUIRE_TREND_ALIGNMENT=True, 69.7% WR
  check_python

  # Verify auth first (will trigger login if needed)
  ensure_auth || exit 1

  local indices="${INDICES:-SENSEX NIFTY50}"
  local capital="${CAPITAL:-100000}"
  local poll_interval="${POLL_INTERVAL:-5}"

  echo -e "${GREEN}Starting FyersN7 Live Trading (Optimized)...${NC}"
  echo -e "  Indices: ${indices}"
  echo -e "  Capital: Rs ${capital}"
  echo -e "  Strategy: 25pt target / 15pt SL"
  echo -e "  Filter: REQUIRE_TREND_ALIGNMENT=True"
  echo -e "  Backtest WR: 69.7%"
  echo ""

  cd "${PROJECT_ROOT}"
  exec "${PYTHON_BIN}" live_fyersn7_trading.py \
    --indices ${indices} \
    --capital "${capital}" \
    --poll-interval "${poll_interval}"
}

cmd_fyersn7_live_background() {
  # Background version of FyersN7 live trading for cmd_all
  local indices="${INDICES:-SENSEX NIFTY50}"
  local capital="${CAPITAL:-100000}"
  local poll_interval="${POLL_INTERVAL:-5}"

  nohup bash -lc "cd '${PROJECT_ROOT}' && exec '${PYTHON_BIN}' live_fyersn7_trading.py --indices ${indices} --capital '${capital}' --poll-interval '${poll_interval}'" > "${PROJECT_ROOT}/logs/fyersn7_live.log" 2>&1 &
  local live_pid=$!
  save_pid "fyersn7_live" "${live_pid}"
  echo -e "${GREEN}  FyersN7 Live Trading started (PID: ${live_pid})${NC}"
}

cmd_fyersn7_paper_background() {
  # Background version of FyersN7 paper trading for cmd_all
  # Uses optimized 25/15 point strategy with REQUIRE_TREND_ALIGNMENT
  local index="${INDEX:-SENSEX}"
  local capital="${CAPITAL:-100000}"

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${YELLOW}  FyersN7 directory not found, skipping...${NC}"
    return 0
  fi

  nohup bash -lc "cd '${FYERSN7_DIR}' && exec env PYTHON_BIN='${PYTHON_BIN}' MARKET_ADAPTER_URL='${MARKET_ADAPTER_URL}' CAPITAL='${capital}' ./scripts/start_all.sh paper" > "${PROJECT_ROOT}/logs/fyersn7_paper.log" 2>&1 &
  local paper_pid=$!
  save_pid "fyersn7_paper" "${paper_pid}"
  echo -e "${GREEN}  FyersN7 Paper Trading started (PID: ${paper_pid})${NC}"
}

cmd_forensics_check() {
  check_python

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  cd "${FYERSN7_DIR}"
  if [[ ! -x "./scripts/start_all.sh" ]]; then
    echo -e "${RED}Error: scripts/start_all.sh not found in ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Running forensics input validation...${NC}"
  exec ./scripts/start_all.sh forensics-check "$@"
}

cmd_quality_check() {
  check_python

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  cd "${FYERSN7_DIR}"
  if [[ ! -x "./scripts/start_all.sh" ]]; then
    echo -e "${RED}Error: scripts/start_all.sh not found in ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Running forensics quality gate...${NC}"
  exec ./scripts/start_all.sh quality-check "$@"
}

cmd_timeline_build() {
  check_python

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  cd "${FYERSN7_DIR}"
  if [[ ! -x "./scripts/start_all.sh" ]]; then
    echo -e "${RED}Error: scripts/start_all.sh not found in ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Running forensics timeline build...${NC}"
  exec ./scripts/start_all.sh timeline-build "$@"
}

cmd_regime_detect() {
  check_python

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  cd "${FYERSN7_DIR}"
  if [[ ! -x "./scripts/start_all.sh" ]]; then
    echo -e "${RED}Error: scripts/start_all.sh not found in ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Running regime detection...${NC}"
  exec ./scripts/start_all.sh regime-detect "$@"
}

cmd_trigger_signals() {
  check_python

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  cd "${FYERSN7_DIR}"
  if [[ ! -x "./scripts/start_all.sh" ]]; then
    echo -e "${RED}Error: scripts/start_all.sh not found in ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Running trigger signal generation...${NC}"
  exec ./scripts/start_all.sh trigger-signals "$@"
}

cmd_pattern_templates() {
  check_python

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  cd "${FYERSN7_DIR}"
  if [[ ! -x "./scripts/start_all.sh" ]]; then
    echo -e "${RED}Error: scripts/start_all.sh not found in ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Running pattern template generation...${NC}"
  exec ./scripts/start_all.sh pattern-templates "$@"
}

cmd_run_summary() {
  check_python

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  cd "${FYERSN7_DIR}"
  if [[ ! -x "./scripts/start_all.sh" ]]; then
    echo -e "${RED}Error: scripts/start_all.sh not found in ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Running forensics run summary generation...${NC}"
  exec ./scripts/start_all.sh run-summary "$@"
}

cmd_canary_metrics() {
  check_python

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  cd "${FYERSN7_DIR}"
  if [[ ! -x "./scripts/start_all.sh" ]]; then
    echo -e "${RED}Error: scripts/start_all.sh not found in ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Running canary metrics generation...${NC}"
  exec ./scripts/start_all.sh canary-metrics "$@"
}

cmd_bot_rules_update() {
  check_python

  if [[ ! -d "${FYERSN7_DIR}" ]]; then
    echo -e "${RED}Error: fyersN7 directory not found at ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  cd "${FYERSN7_DIR}"
  if [[ ! -x "./scripts/start_all.sh" ]]; then
    echo -e "${RED}Error: scripts/start_all.sh not found in ${FYERSN7_DIR}${NC}" >&2
    exit 1
  fi

  echo -e "${GREEN}Running bot rules update generation...${NC}"
  ./scripts/start_all.sh bot-rules-update "$@"
  local exit_code=$?

  if [[ ${exit_code} -eq 0 ]]; then
    mkdir -p "${PROJECT_ROOT}/logs" 2>/dev/null || true
    date +%Y-%m-%d > "${PROJECT_ROOT}/logs/last_bot_rules_run" || true
  fi

  return ${exit_code}
}

cmd_market_report() {
  check_python
  ensure_market_adapter
  cd "${PROJECT_ROOT}"
  PYTHONWARNINGS="${PYTHONWARNINGS:-ignore}" exec "${PYTHON_BIN}" -m shared_project_engine.market.report "$@"
}

cmd_market_audit() {
  check_python
  cd "${PROJECT_ROOT}"
  PYTHONWARNINGS="${PYTHONWARNINGS:-ignore}" exec "${PYTHON_BIN}" -m shared_project_engine.market.audit "$@"
}

# ============================================================================
# Scalping Engine Commands
# ============================================================================

cmd_scalping() {
  check_python

  if [[ ! -d "${BOT_ARMY_DIR}/scalping" ]]; then
    echo -e "${RED}Error: Scalping directory not found at ${BOT_ARMY_DIR}/scalping${NC}" >&2
    exit 1
  fi

  local live_flag=""
  if [[ "${SCALPING_LIVE:-0}" == "1" ]]; then
    live_flag="--live"
    echo -e "${RED}WARNING: LIVE TRADING MODE${NC}"
  fi

  local interval="${SCALPING_INTERVAL:-5}"

  echo -e "${GREEN}Starting 21-Agent Scalping System...${NC}"
  echo -e "  Mode: ${SCALPING_LIVE:-0} == 1 && echo 'LIVE' || echo 'DRY RUN'}"
  echo -e "  Interval: ${interval}s"
  echo -e "  Market Hours: 8:58 AM - 3:40 PM IST"
  echo ""

  cd "${BOT_ARMY_DIR}"
  exec "${PYTHON_BIN}" -m scalping.launcher ${live_flag} --interval "${interval}" --respect-hours "$@"
}

cmd_scalping_api_background() {
  check_python

  if [[ ! -d "${BOT_ARMY_DIR}/scalping" ]]; then
    echo -e "${RED}Error: Scalping directory not found${NC}" >&2
    return 1
  fi

  local port="${SCALPING_API_PORT:-8002}"

  mkdir -p "${PROJECT_ROOT}/logs"

  nohup bash -lc "cd '${BOT_ARMY_DIR}' && exec '${PYTHON_BIN}' -m uvicorn scalping.api:app --host 0.0.0.0 --port '${port}'" > "${PROJECT_ROOT}/logs/scalping_api.log" 2>&1 &
  local api_pid=$!
  save_pid "scalping_api" "${api_pid}"
  echo -e "${GREEN}  Scalping API started on port ${port} (PID: ${api_pid})${NC}"
}

cmd_scalping_background() {
  check_python

  if [[ ! -d "${BOT_ARMY_DIR}/scalping" ]]; then
    echo -e "${RED}Error: Scalping directory not found${NC}" >&2
    return 1
  fi

  local live_flag=""
  if [[ "${SCALPING_LIVE:-0}" == "1" ]]; then
    live_flag="--live"
  fi

  local interval="${SCALPING_INTERVAL:-5}"

  mkdir -p "${PROJECT_ROOT}/logs"

  nohup bash -lc "cd '${BOT_ARMY_DIR}' && exec '${PYTHON_BIN}' -m scalping.launcher ${live_flag} --interval '${interval}' --respect-hours" > "${PROJECT_ROOT}/logs/scalping.log" 2>&1 &
  local scalping_pid=$!
  save_pid "scalping" "${scalping_pid}"
  echo -e "${GREEN}  Scalping Engine started (PID: ${scalping_pid})${NC}"
}

# ============================================================================
# LLM Debate Commands
# ============================================================================

cmd_llm_debate() {
  check_python

  if [[ ! -d "${LLM_DEBATE_DIR}/backend" ]]; then
    echo -e "${RED}Error: LLM Debate directory not found at ${LLM_DEBATE_DIR}/backend${NC}" >&2
    exit 1
  fi

  local port="${LLM_DEBATE_PORT:-8080}"
  local debate_python
  debate_python="$(llm_debate_python_bin)"

  echo -e "${GREEN}Starting LLM Debate Backend...${NC}"
  echo -e "  Port: ${port}"
  echo ""

  cd "${LLM_DEBATE_DIR}/backend"
  exec "${debate_python}" -m uvicorn main:app --host 0.0.0.0 --port "${port}" "$@"
}

cmd_llm_debate_background() {
  check_python

  if [[ ! -d "${LLM_DEBATE_DIR}/backend" ]]; then
    echo -e "${RED}Error: LLM Debate directory not found${NC}" >&2
    return 1
  fi

  local port="${LLM_DEBATE_PORT:-8080}"
  local debate_python
  debate_python="$(llm_debate_python_bin)"

  mkdir -p "${PROJECT_ROOT}/logs"

  # Sync API keys from main .env to llm_debate backend .env
  local debate_env="${LLM_DEBATE_DIR}/backend/.env"
  if [[ -f "${AUTH_ENV_FILE}" ]]; then
    # Extract LLM API keys and sync to debate backend
    local anthropic_key openai_key
    anthropic_key=$(grep "^ANTHROPIC_API_KEY=" "${AUTH_ENV_FILE}" 2>/dev/null | cut -d= -f2-)
    openai_key=$(grep "^OPENAI_API_KEY=" "${AUTH_ENV_FILE}" 2>/dev/null | cut -d= -f2-)

    # Only create/update if keys exist
    if [[ -n "${anthropic_key}" ]] || [[ -n "${openai_key}" ]]; then
      # Preserve existing content, update keys
      local existing_content=""
      [[ -f "${debate_env}" ]] && existing_content=$(grep -v "^ANTHROPIC_API_KEY\|^OPENAI_API_KEY" "${debate_env}" 2>/dev/null || true)
      {
        echo "${existing_content}"
        [[ -n "${anthropic_key}" ]] && echo "ANTHROPIC_API_KEY=${anthropic_key}"
        [[ -n "${openai_key}" ]] && echo "OPENAI_API_KEY=${openai_key}"
      } | grep -v "^$" > "${debate_env}"
      echo -e "${CYAN}  Synced LLM API keys to debate backend${NC}"
    fi
  fi

  nohup bash -lc "cd '${LLM_DEBATE_DIR}/backend' && exec '${debate_python}' -m uvicorn main:app --host 0.0.0.0 --port '${port}'" > "${PROJECT_ROOT}/logs/llm_debate.log" 2>&1 &
  local debate_pid=$!
  save_pid "llm_debate" "${debate_pid}"
  echo -e "${GREEN}  LLM Debate Backend started (PID: ${debate_pid})${NC}"
}

cmd_both() {
  check_python

  # Verify auth first (will trigger login if needed)
  ensure_auth || exit 1

  echo -e "${GREEN}Starting Both Systems...${NC}"
  echo ""

  # Stop any existing processes
  stop_all

  # Create log directory
  mkdir -p "${PROJECT_ROOT}/logs"
  mkdir -p "${CLAWWORK_DIR}/logs"
  mkdir -p "${CLAWWORK_DIR}/livebench/data"

  if [[ "${BOT_RULES_CRON_AUTOINSTALL:-1}" == "1" ]]; then
    ensure_bot_rules_cron
  fi

  # Port configuration
  API_PORT="${API_PORT:-8001}"
  FRONTEND_PORT="${FRONTEND_PORT:-3001}"

  # ============================================
  # 1. Start Shared Market Adapter
  # ============================================
  echo -e "${BLUE}[1/5] Starting shared Market Adapter...${NC}"
  ensure_market_adapter

  # ============================================
  # 2. Start fyersN7 Signal Engines
  # ============================================
  echo -e "${BLUE}[2/5] Starting fyersN7 Signal Engines...${NC}"
  if [[ -d "${FYERSN7_DIR}" ]]; then
    # Sync credentials
    if [[ -n "${FYERS_ACCESS_TOKEN:-}" ]]; then
      cat > "${FYERSN7_DIR}/.fyers.env" << EOF
FYERS_CLIENT_ID=${FYERS_CLIENT_ID:-}
FYERS_SECRET_KEY=${FYERS_SECRET_KEY:-}
FYERS_REDIRECT_URI=${DEFAULT_REDIRECT_URI}
FYERS_ACCESS_TOKEN=${FYERS_ACCESS_TOKEN:-}
EOF
    fi

    nohup bash -lc "cd '${FYERSN7_DIR}' && exec env PYTHON_BIN='${PYTHON_BIN}' ENABLE_WEB_VIEW=1 MARKET_ADAPTER_URL='${MARKET_ADAPTER_URL}' ./scripts/start_all.sh run" > "${PROJECT_ROOT}/logs/fyersn7.log" 2>&1 &
    local fyersn7_wrapper_pid=$!
    save_pid "fyersn7-wrapper" "${fyersn7_wrapper_pid}"
    sleep 2
    register_fyersn7_workers
    echo -e "${GREEN}  fyersN7 started (PID: ${fyersn7_wrapper_pid})${NC}"
  fi

  # ============================================
  # 3. Start Backend API Server
  # ============================================
  echo -e "${BLUE}[3/5] Starting Backend API on port ${API_PORT}...${NC}"
  local livebench_python
  livebench_python="$(livebench_python_bin)"
  local auto_trader_strategy_id="${AUTO_TRADER_STRATEGY_ID:-clawwork-autotrader}"
  nohup bash -lc "cd '${CLAWWORK_DIR}/livebench' && export AUTO_TRADER_STRATEGY_ID='${auto_trader_strategy_id}' && exec '${livebench_python}' -m uvicorn api.server:app --host 0.0.0.0 --port '${API_PORT}'" > "${CLAWWORK_DIR}/logs/api_server.log" 2>&1 &
  API_PID=$!
  save_pid "api" ${API_PID}
  echo -e "${GREEN}  API Server started (PID: ${API_PID})${NC}"

  # Wait for API to be ready
  echo -n "  Waiting for API..."
  for i in {1..30}; do
    if curl -s "http://localhost:${API_PORT}/api/" > /dev/null 2>&1; then
      echo -e " ${GREEN}ready${NC}"
      break
    fi
    sleep 1
    echo -n "."
  done

  # ============================================
  # 4. Start Frontend Dashboard
  # ============================================
  echo -e "${BLUE}[4/5] Starting Frontend on port ${FRONTEND_PORT}...${NC}"
  if ! ensure_frontend_dependencies; then
    stop_all
    exit 1
  fi
  nohup bash -lc "cd '${CLAWWORK_DIR}/frontend' && exec npm run dev -- --host 0.0.0.0 --port '${FRONTEND_PORT}'" > "${CLAWWORK_DIR}/logs/frontend.log" 2>&1 &
  FRONTEND_PID=$!
  save_pid "frontend" ${FRONTEND_PID}
  echo -e "${GREEN}  Frontend started (PID: ${FRONTEND_PID})${NC}"

  # Wait for Frontend to be ready
  echo -n "  Waiting for Frontend..."
  for i in {1..30}; do
    if curl -s "http://localhost:${FRONTEND_PORT}/" > /dev/null 2>&1; then
      echo -e " ${GREEN}ready${NC}"
      break
    fi
    sleep 1
    echo -n "."
  done

  # ============================================
  # 5. Start Fyers Screener Loop (15s refresh)
  # ============================================
  echo -e "${BLUE}[5/5] AutoTrader is managed by the API startup hook (paper mode).${NC}"
  start_fyers_screener_loop "[5/5]" || true

  echo ""
  echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║   All Systems Running                                         ║${NC}"
  echo -e "${GREEN}╠═══════════════════════════════════════════════════════════════╣${NC}"
  echo -e "${GREEN}║   Dashboard:     http://localhost:${FRONTEND_PORT}                         ║${NC}"
  echo -e "${GREEN}║   FyersN7 Web:   http://localhost:8787                          ║${NC}"
  echo -e "${GREEN}║   API Server:    http://localhost:${API_PORT}/api                       ║${NC}"
  echo -e "${GREEN}║   Adapter:       ${MARKET_ADAPTER_URL}                  ║${NC}"
  echo -e "${GREEN}║   API Docs:      http://localhost:${API_PORT}/docs                      ║${NC}"
  echo -e "${GREEN}╠═══════════════════════════════════════════════════════════════╣${NC}"
  echo -e "${GREEN}║   Paper Trading: AutoTrader (API-managed)                       ║${NC}"
  echo -e "${GREEN}║   Strategy ID:  ${AUTO_TRADER_STRATEGY_ID:-clawwork-autotrader}                             ║${NC}"
  echo -e "${GREEN}║   Logs:          ${PROJECT_ROOT}/logs/               ║${NC}"
  echo -e "${GREEN}║   Stop:          ./start.sh stop                              ║${NC}"
  echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
  echo ""

  if should_detach; then
    echo -e "${YELLOW}Detached startup complete. Use './start.sh logs' to inspect logs and './start.sh stop' to stop services.${NC}"
    return 0
  fi

  echo -e "${YELLOW}Press Ctrl+C to stop all processes...${NC}"
  monitor_processes
}

cmd_dashboard() {
  check_python

  echo -e "${GREEN}Starting Dashboard Only...${NC}"
  echo ""

  # Stop any existing processes
  stop_all

  # Create log directory
  mkdir -p "${PROJECT_ROOT}/logs"
  mkdir -p "${CLAWWORK_DIR}/logs"
  mkdir -p "${CLAWWORK_DIR}/livebench/data"

  # Port configuration
  API_PORT="${API_PORT:-8001}"
  FRONTEND_PORT="${FRONTEND_PORT:-3001}"

  # Start Backend API Server
  echo -e "${BLUE}[1/3] Starting Backend API on port ${API_PORT}...${NC}"
  local livebench_python
  livebench_python="$(livebench_python_bin)"
  local auto_trader_strategy_id="${AUTO_TRADER_STRATEGY_ID:-clawwork-autotrader}"
  nohup bash -lc "cd '${CLAWWORK_DIR}/livebench' && export AUTO_TRADER_STRATEGY_ID='${auto_trader_strategy_id}' && exec '${livebench_python}' -m uvicorn api.server:app --host 0.0.0.0 --port '${API_PORT}'" > "${CLAWWORK_DIR}/logs/api_server.log" 2>&1 &
  API_PID=$!
  save_pid "api" ${API_PID}
  echo -e "${GREEN}  API Server started (PID: ${API_PID})${NC}"

  # Wait for API to be ready
  echo -n "  Waiting for API..."
  for i in {1..30}; do
    if curl -s "http://localhost:${API_PORT}/api/" > /dev/null 2>&1; then
      echo -e " ${GREEN}ready${NC}"
      break
    fi
    sleep 1
    echo -n "."
  done

  # Start Frontend Dashboard
  echo -e "${BLUE}[2/3] Starting Frontend on port ${FRONTEND_PORT}...${NC}"
  if ! ensure_frontend_dependencies; then
    stop_all
    exit 1
  fi
  nohup bash -lc "cd '${CLAWWORK_DIR}/frontend' && exec npm run dev -- --host 0.0.0.0 --port '${FRONTEND_PORT}'" > "${CLAWWORK_DIR}/logs/frontend.log" 2>&1 &
  FRONTEND_PID=$!
  save_pid "frontend" ${FRONTEND_PID}
  echo -e "${GREEN}  Frontend started (PID: ${FRONTEND_PID})${NC}"

  # Wait for Frontend to be ready
  echo -n "  Waiting for Frontend..."
  for i in {1..30}; do
    if curl -s "http://localhost:${FRONTEND_PORT}/" > /dev/null 2>&1; then
      echo -e " ${GREEN}ready${NC}"
      break
    fi
    sleep 1
    echo -n "."
  done

  # Start Scalping Dashboard API so /scalping works in dashboard mode too.
  SCALPING_API_PORT="${SCALPING_API_PORT:-8002}"
  export SCALPING_ENGINE_ENABLED="${SCALPING_ENGINE_ENABLED:-1}"
  export SCALPING_LIVE="${SCALPING_LIVE:-0}"
  export SCALPING_INTERVAL="${SCALPING_INTERVAL:-5}"
  echo -e "${BLUE}[3/3] Starting Scalping Dashboard API on port ${SCALPING_API_PORT}...${NC}"
  if [[ -d "${BOT_ARMY_DIR}/scalping" ]]; then
    cmd_scalping_api_background
    echo -n "  Waiting for Scalping API..."
    for i in {1..30}; do
      if curl -s "http://localhost:${SCALPING_API_PORT}/api/scalping/status" > /dev/null 2>&1; then
        echo -e " ${GREEN}ready${NC}"
        break
      fi
      sleep 1
      echo -n "."
    done
    echo -e "${GREEN}    Engine embedded in API (SCALPING_ENGINE_ENABLED=${SCALPING_ENGINE_ENABLED})${NC}"
  else
    echo -e "${YELLOW}  Scalping API not found, skipping...${NC}"
  fi

  echo ""
  echo -e "${GREEN}Dashboard is running!${NC}"
  echo -e "  Dashboard: http://localhost:${FRONTEND_PORT}"
  echo -e "  Scalping: http://localhost:${FRONTEND_PORT}/scalping"
  echo -e "  API: http://localhost:${API_PORT}/api"
  echo -e "  Scalping API: http://localhost:${SCALPING_API_PORT}/api/scalping"
  echo -e "  Docs: http://localhost:${API_PORT}/docs"
  echo ""
  echo -e "${YELLOW}Note: fyersN7 signal engines are not started in dashboard mode. Use './start.sh both' or './start.sh all' for the full stack.${NC}"
  echo ""

  if should_detach; then
    echo -e "${YELLOW}Detached startup complete. Use './start.sh logs' to inspect logs and './start.sh stop' to stop services.${NC}"
    return 0
  fi

  echo -e "${YELLOW}Press Ctrl+C to stop...${NC}"
  monitor_processes
}

cmd_all() {
  check_python

  # Verify auth first (will trigger login if needed)
  ensure_auth || exit 1

  echo -e "${GREEN}Starting ALL Systems (Full Stack + Scalping + LLM Debate)...${NC}"
  echo ""

  # Stop any existing processes
  stop_all

  # Create log directory
  mkdir -p "${PROJECT_ROOT}/logs"
  mkdir -p "${CLAWWORK_DIR}/logs"
  mkdir -p "${CLAWWORK_DIR}/livebench/data"

  if [[ "${BOT_RULES_CRON_AUTOINSTALL:-1}" == "1" ]]; then
    ensure_bot_rules_cron
  fi

  # Install scalping cron (8:58 AM Mon-Fri)
  if [[ "${SCALPING_CRON_AUTOINSTALL:-1}" == "1" ]]; then
    ensure_scalping_cron
  fi

  # Port configuration
  API_PORT="${API_PORT:-8001}"
  FRONTEND_PORT="${FRONTEND_PORT:-3001}"
  LLM_DEBATE_PORT="${LLM_DEBATE_PORT:-8080}"

  # ============================================
  # 1. Start Shared Market Adapter
  # ============================================
  echo -e "${BLUE}[1/9] Starting shared Market Adapter...${NC}"
  ensure_market_adapter

  # ============================================
  # 2. Start LLM Debate Backend
  # ============================================
  echo -e "${BLUE}[2/9] Starting LLM Debate Backend on port ${LLM_DEBATE_PORT}...${NC}"
  if [[ -d "${LLM_DEBATE_DIR}/backend" ]]; then
    cmd_llm_debate_background
  else
    echo -e "${YELLOW}  LLM Debate not found, skipping...${NC}"
  fi

  # ============================================
  # 3. Start fyersN7 Signal Engines
  # ============================================
  echo -e "${BLUE}[3/9] Starting fyersN7 Signal Engines...${NC}"
  if [[ -d "${FYERSN7_DIR}" ]]; then
    # Sync credentials
    if [[ -n "${FYERS_ACCESS_TOKEN:-}" ]]; then
      cat > "${FYERSN7_DIR}/.fyers.env" << EOF
FYERS_CLIENT_ID=${FYERS_CLIENT_ID:-}
FYERS_SECRET_KEY=${FYERS_SECRET_KEY:-}
FYERS_REDIRECT_URI=${DEFAULT_REDIRECT_URI}
FYERS_ACCESS_TOKEN=${FYERS_ACCESS_TOKEN:-}
EOF
    fi

    # Disable paper trading in signal engines - step 8 handles paper trading separately
    nohup bash -lc "cd '${FYERSN7_DIR}' && exec env PYTHON_BIN='${PYTHON_BIN}' ENABLE_PAPER_TRADING=0 ENABLE_WEB_VIEW=1 MARKET_ADAPTER_URL='${MARKET_ADAPTER_URL}' ./scripts/start_all.sh run" > "${PROJECT_ROOT}/logs/fyersn7.log" 2>&1 &
    local fyersn7_wrapper_pid=$!
    save_pid "fyersn7-wrapper" "${fyersn7_wrapper_pid}"
    sleep 2
    register_fyersn7_workers
    echo -e "${GREEN}  fyersN7 started (PID: ${fyersn7_wrapper_pid})${NC}"
  fi

  # ============================================
  # 4. Start Backend API Server
  # ============================================
  echo -e "${BLUE}[4/9] Starting Backend API on port ${API_PORT}...${NC}"
  local livebench_python
  livebench_python="$(livebench_python_bin)"
  local auto_trader_strategy_id="${AUTO_TRADER_STRATEGY_ID:-clawwork-autotrader}"
  nohup bash -lc "cd '${CLAWWORK_DIR}/livebench' && export AUTO_TRADER_STRATEGY_ID='${auto_trader_strategy_id}' && exec '${livebench_python}' -m uvicorn api.server:app --host 0.0.0.0 --port '${API_PORT}'" > "${CLAWWORK_DIR}/logs/api_server.log" 2>&1 &
  API_PID=$!
  save_pid "api" ${API_PID}
  echo -e "${GREEN}  API Server started (PID: ${API_PID})${NC}"

  # Wait for API to be ready
  echo -n "  Waiting for API..."
  for i in {1..30}; do
    if curl -s "http://localhost:${API_PORT}/api/" > /dev/null 2>&1; then
      echo -e " ${GREEN}ready${NC}"
      break
    fi
    sleep 1
    echo -n "."
  done

  # ============================================
  # 5. Start Frontend Dashboard
  # ============================================
  echo -e "${BLUE}[5/9] Starting Frontend on port ${FRONTEND_PORT}...${NC}"
  if ! ensure_frontend_dependencies; then
    stop_all
    exit 1
  fi
  nohup bash -lc "cd '${CLAWWORK_DIR}/frontend' && exec npm run dev -- --host 0.0.0.0 --port '${FRONTEND_PORT}'" > "${CLAWWORK_DIR}/logs/frontend.log" 2>&1 &
  FRONTEND_PID=$!
  save_pid "frontend" ${FRONTEND_PID}
  echo -e "${GREEN}  Frontend started (PID: ${FRONTEND_PID})${NC}"

  # Wait for Frontend to be ready
  echo -n "  Waiting for Frontend..."
  for i in {1..30}; do
    if curl -s "http://localhost:${FRONTEND_PORT}/" > /dev/null 2>&1; then
      echo -e " ${GREEN}ready${NC}"
      break
    fi
    sleep 1
    echo -n "."
  done

  # ============================================
  # 6. AutoTrader is API-managed
  # ============================================
  echo -e "${BLUE}[6/9] AutoTrader is managed by the API startup hook (paper mode).${NC}"

  # ============================================
  # 7. Start Fyers Screener Loop (15s refresh)
  # ============================================
  start_fyers_screener_loop "[7/9]" || true

  # ============================================
  # 8. Start Scalping API (Dashboard backend)
  # ============================================
  SCALPING_API_PORT="${SCALPING_API_PORT:-8002}"
  # Engine now runs embedded in API by default (SCALPING_ENGINE_ENABLED=1)
  export SCALPING_ENGINE_ENABLED="${SCALPING_ENGINE_ENABLED:-1}"
  export SCALPING_LIVE="${SCALPING_LIVE:-0}"
  export SCALPING_INTERVAL="${SCALPING_INTERVAL:-5}"
  echo -e "${BLUE}[8/9] Starting Scalping Dashboard API with embedded engine on port ${SCALPING_API_PORT}...${NC}"
  if [[ -d "${BOT_ARMY_DIR}/scalping" ]]; then
    cmd_scalping_api_background
    echo -e "${GREEN}    Engine embedded in API (SCALPING_ENGINE_ENABLED=${SCALPING_ENGINE_ENABLED})${NC}"
  else
    echo -e "${YELLOW}  Scalping API not found, skipping...${NC}"
  fi

  # Skip separate engine - now embedded in API
  # To run separate engine instead, set SCALPING_ENGINE_ENABLED=0

  # ============================================
  # 9. Start FyersN7 Paper Trading (Optimized)
  # ============================================
  echo -e "${BLUE}[9/9] Starting FyersN7 Paper Trading (69.7% WR strategy)...${NC}"
  cmd_fyersn7_paper_background

  echo ""
  echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║   ALL Systems Running (Full Stack)                            ║${NC}"
  echo -e "${GREEN}╠═══════════════════════════════════════════════════════════════╣${NC}"
  echo -e "${GREEN}║   Dashboard:     http://localhost:${FRONTEND_PORT}                         ║${NC}"
  echo -e "${GREEN}║   FyersN7 Web:   http://localhost:8787                          ║${NC}"
  echo -e "${GREEN}║   Scalping:      http://localhost:${FRONTEND_PORT}/scalping                ║${NC}"
  echo -e "${GREEN}║   API Server:    http://localhost:${API_PORT}/api                       ║${NC}"
  echo -e "${GREEN}║   Scalping API:  http://localhost:${SCALPING_API_PORT}/api/scalping             ║${NC}"
  echo -e "${GREEN}║   LLM Debate:    http://localhost:${LLM_DEBATE_PORT}                          ║${NC}"
  echo -e "${GREEN}║   Adapter:       ${MARKET_ADAPTER_URL}                  ║${NC}"
  echo -e "${GREEN}╠═══════════════════════════════════════════════════════════════╣${NC}"
  echo -e "${GREEN}║   Engines:                                                     ║${NC}"
  echo -e "${GREEN}║     - fyersN7 Signal Engine                                   ║${NC}"
  echo -e "${GREEN}║     - fyersN7 Paper Trading (25/15pt, 69.7% WR)               ║${NC}"
  echo -e "${GREEN}║     - AutoTrader paper trading (API-managed)                  ║${NC}"
  echo -e "${GREEN}║       strategy=${AUTO_TRADER_STRATEGY_ID:-clawwork-autotrader}                            ║${NC}"
  echo -e "${GREEN}║     - 21-Agent Scalping (8:58 AM - 3:40 PM)                   ║${NC}"
  echo -e "${GREEN}║     - LLM Debate Backend                                      ║${NC}"
  echo -e "${GREEN}╠═══════════════════════════════════════════════════════════════╣${NC}"
  echo -e "${GREEN}║   Logs:          ${PROJECT_ROOT}/logs/               ║${NC}"
  echo -e "${GREEN}║   Stop:          ./start.sh stop                              ║${NC}"
  echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
  echo ""

  if should_detach; then
    echo -e "${YELLOW}Detached startup complete. Use './start.sh logs' to inspect logs and './start.sh stop' to stop services.${NC}"
    return 0
  fi

  echo -e "${YELLOW}Press Ctrl+C to stop all processes...${NC}"
  monitor_processes
}

cmd_logs() {
  local log_dir="${PROJECT_ROOT}/logs"

  if [[ ! -d "${log_dir}" ]]; then
    echo -e "${YELLOW}No logs directory found${NC}"
    exit 0
  fi

  echo -e "${BLUE}Tailing logs from ${log_dir}...${NC}"
  echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
  echo ""

  tail -f "${log_dir}"/*.log 2>/dev/null || echo "No log files found"
}

# ============================================================================
# Main
# ============================================================================

main() {
  local action="${1:-menu}"
  shift 2>/dev/null || true

  case "${action}" in
    login)
      print_banner
      cmd_login "$@"
      ;;
    status)
      print_banner
      cmd_status "$@"
      ;;
    test)
      print_banner
      cmd_test "$@"
      ;;
    forensics-check|forensics|validate)
      print_banner
      cmd_forensics_check "$@"
      ;;
    quality-check|quality)
      print_banner
      cmd_quality_check "$@"
      ;;
    timeline-build|timeline)
      print_banner
      cmd_timeline_build "$@"
      ;;
    regime-detect|regime)
      print_banner
      cmd_regime_detect "$@"
      ;;
    trigger-signals|trigger|triggers)
      print_banner
      cmd_trigger_signals "$@"
      ;;
    pattern-templates|patterns)
      print_banner
      cmd_pattern_templates "$@"
      ;;
    run-summary|summary)
      print_banner
      cmd_run_summary "$@"
      ;;
    canary-metrics|canary)
      print_banner
      cmd_canary_metrics "$@"
      ;;
    bot-rules-update|bot-rules|rules-update)
      print_banner
      cmd_bot_rules_update "$@"
      ;;
    market-report|adapter-report|market-metrics|metrics)
      print_banner
      cmd_market_report "$@"
      ;;
    market-audit|adapter-audit|audit)
      print_banner
      cmd_market_audit "$@"
      ;;
    clawwork|claw)
      print_banner
      cmd_clawwork
      ;;
    fyersn7|fyers|signal)
      print_banner
      cmd_fyersn7
      ;;
    fyersn7-paper|fyers-paper|paper)
      print_banner
      cmd_fyersn7_paper
      ;;
    fyersn7-live|fyers-live|live)
      print_banner
      cmd_fyersn7_live
      ;;
    both)
      print_banner
      cmd_both
      ;;
    all|full)
      print_banner
      cmd_all
      ;;
    scalping|scalp|agents)
      print_banner
      cmd_scalping "$@"
      ;;
    llm-debate|debate|llm)
      print_banner
      cmd_llm_debate "$@"
      ;;
    dashboard|dash|web)
      print_banner
      cmd_dashboard
      ;;
    stop)
      print_banner
      stop_all
      ;;
    logs)
      cmd_logs
      ;;
    menu|help|-h|--help|"")
      print_banner
      print_menu
      ;;
    *)
      echo -e "${RED}Unknown command: ${action}${NC}" >&2
      print_menu
      exit 1
      ;;
  esac
}

main "$@"
