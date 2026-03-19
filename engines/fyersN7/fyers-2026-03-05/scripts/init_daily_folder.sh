#!/usr/bin/env bash
# Helper script to initialize daily postmortem folder with per-index support
# Source this from other scripts: source "${ROOT_DIR}/scripts/init_daily_folder.sh"
# Set INDEX env var before sourcing to specify index (default: SENSEX)
#
# All data files (CSV + state JSON) are now stored in daily folders:
#   postmortem/YYYY-MM-DD/INDEX/
#     - decision_journal.csv
#     - signals.csv
#     - paper_trades.csv
#     - paper_equity.csv
#     - opportunity_events.csv
#     - .signal_state.json
#     - .opportunity_engine_state.json
#     - .paper_trade_state.json
#
# Exports:
#   MARKET_OPEN=1 if market is open, 0 if closed
#   is_market_open() function returns 0 (success) if open, 1 if closed

# Index name (SENSEX, BANKNIFTY, NIFTY, etc.)
INDEX="${INDEX:-SENSEX}"

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

# =============================================================================
# MARKET HOURS CHECK (IST)
# =============================================================================
# Load market hours from shared_project_engine.market (single source of truth)
_load_market_hours() {
  local config
  local market_hours_python="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"

  if [[ "${market_hours_python}" != */* ]]; then
    market_hours_python="$(command -v "${market_hours_python}" 2>/dev/null || true)"
  fi

  if [[ -z "${market_hours_python}" ]] || [[ ! -x "${market_hours_python}" ]]; then
    market_hours_python="/usr/bin/python3"
  fi

  config=$("${market_hours_python}" -c "
import sys
sys.path.insert(0, '${PROJECT_ROOT}')
try:
    from shared_project_engine.market import print_market_hours_for_shell
    print_market_hours_for_shell()
except ImportError:
    # Fallback defaults
    print('MARKET_OPEN_HOUR=9')
    print('MARKET_OPEN_MIN=0')
    print('MARKET_CLOSE_HOUR=15')
    print('MARKET_CLOSE_MIN=45')
    print('PRE_OPEN_HOUR=9')
    print('PRE_OPEN_MIN=0')
    print('POST_CLOSE_HOUR=15')
    print('POST_CLOSE_MIN=45')
" 2>/dev/null) || true
  eval "${config}"
}
_load_market_hours

# Use buffer hours for data collection (pre-open to post-close)
MARKET_OPEN_HOUR="${PRE_OPEN_HOUR:-9}"
MARKET_OPEN_MIN="${PRE_OPEN_MIN:-0}"
MARKET_CLOSE_HOUR="${POST_CLOSE_HOUR:-15}"
MARKET_CLOSE_MIN="${POST_CLOSE_MIN:-45}"

is_market_open() {
  local day_of_week hour minute

  if command -v gdate &>/dev/null; then
    day_of_week="$(TZ='Asia/Kolkata' gdate '+%u')"  # 1=Mon, 7=Sun
    hour="$(TZ='Asia/Kolkata' gdate '+%-H')"
    minute="$(TZ='Asia/Kolkata' gdate '+%-M')"
  else
    day_of_week="$(TZ='Asia/Kolkata' date '+%u')"
    hour="$(TZ='Asia/Kolkata' date '+%-H')"
    minute="$(TZ='Asia/Kolkata' date '+%-M')"
  fi

  # Weekend check (Saturday=6, Sunday=7)
  if [[ "${day_of_week}" -ge 6 ]]; then
    return 1
  fi

  # Convert to minutes since midnight for easy comparison
  local now_mins=$((hour * 60 + minute))
  local open_mins=$((MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MIN))
  local close_mins=$((MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MIN))

  if [[ "${now_mins}" -ge "${open_mins}" && "${now_mins}" -le "${close_mins}" ]]; then
    return 0  # Market is open
  else
    return 1  # Market is closed
  fi
}

# Export market status
if is_market_open; then
  export MARKET_OPEN=1
else
  export MARKET_OPEN=0
fi

# =============================================================================
# DATE AND FOLDER SETUP
# =============================================================================

# Get today's date in IST timezone
if command -v gdate &>/dev/null; then
  TODAY_DATE="$(TZ='Asia/Kolkata' gdate '+%Y-%m-%d')"
else
  TODAY_DATE="$(TZ='Asia/Kolkata' date '+%Y-%m-%d')"
fi

# Create postmortem folder structure: postmortem/YYYY-MM-DD/INDEX/
POSTMORTEM_DIR="${ROOT_DIR}/postmortem"
DAILY_DIR="${POSTMORTEM_DIR}/${TODAY_DATE}"
INDEX_DIR="${DAILY_DIR}/${INDEX}"

# Check if this is a new day (compare with marker file per index)
LAST_DATE_FILE="${ROOT_DIR}/.last_run_date_${INDEX}"
IS_NEW_DAY=0

if [[ -f "${LAST_DATE_FILE}" ]]; then
  LAST_DATE="$(cat "${LAST_DATE_FILE}")"
  if [[ "${LAST_DATE}" != "${TODAY_DATE}" ]]; then
    IS_NEW_DAY=1
  fi
else
  IS_NEW_DAY=1
fi

if [[ ! -d "${INDEX_DIR}" ]]; then
  mkdir -p "${INDEX_DIR}"
  echo "Created index folder: ${INDEX_DIR}"
fi

# State file paths (now in daily folder)
SIGNAL_STATE="${INDEX_DIR}/.signal_state.json"
OPP_STATE="${INDEX_DIR}/.opportunity_engine_state.json"
PAPER_STATE="${INDEX_DIR}/.paper_trade_state.json"

# Initialize fresh state files for new day
if [[ "${IS_NEW_DAY}" == "1" ]]; then
  echo "New trading day detected for ${INDEX}: ${TODAY_DATE}"

  # Create fresh signal state
  if [[ ! -f "${SIGNAL_STATE}" ]]; then
    echo '{"last_side": null, "last_ts": 0, "flip_count": 0, "stable_count": 0}' > "${SIGNAL_STATE}"
    echo "  Created fresh signal state"
  fi

  # Create fresh opportunity engine state
  if [[ ! -f "${OPP_STATE}" ]]; then
    echo '{"open_positions": {}, "processed_rows": 0, "closed_trades": []}' > "${OPP_STATE}"
    echo "  Created fresh opportunity engine state"
  fi

  # Create fresh paper trade state
  if [[ ! -f "${PAPER_STATE}" ]]; then
    echo '{"open_positions": [], "processed_rows": 0, "closed_trades": [], "total_pnl": 0}' > "${PAPER_STATE}"
    echo "  Created fresh paper trade state"
  fi

  # Update last run date marker for this index
  echo "${TODAY_DATE}" > "${LAST_DATE_FILE}"
fi

# Export per-index CSV paths
export JOURNAL_CSV="${JOURNAL_CSV:-${INDEX_DIR}/decision_journal.csv}"
export SIGNALS_CSV="${SIGNALS_CSV:-${INDEX_DIR}/signals.csv}"
export TRADES_CSV="${TRADES_CSV:-${INDEX_DIR}/paper_trades.csv}"
export EQUITY_CSV="${EQUITY_CSV:-${INDEX_DIR}/paper_equity.csv}"
export EVENTS_CSV="${EVENTS_CSV:-${INDEX_DIR}/opportunity_events.csv}"

# Keep the expected daily CSV files present even before the first signal/trade row is written.
touch "${JOURNAL_CSV}" "${SIGNALS_CSV}" "${TRADES_CSV}" "${EQUITY_CSV}" "${EVENTS_CSV}"

# Export per-index state file paths (now in daily folder)
export SIGNAL_STATE_FILE="${SIGNAL_STATE_FILE:-${SIGNAL_STATE}}"
export OPP_STATE_FILE="${OPP_STATE_FILE:-${OPP_STATE}}"
export PAPER_STATE_FILE="${PAPER_STATE_FILE:-${PAPER_STATE}}"

release_instance_locks() {
  local lock_dir
  for lock_dir in ${ACTIVE_INSTANCE_LOCK_DIRS:-}; do
    rm -rf "${lock_dir}" 2>/dev/null || true
  done
}

acquire_instance_lock() {
  local lock_name="${1:?lock name required}"
  local lock_dir="${INDEX_DIR}/.${lock_name}.lock"
  local pid_file="${lock_dir}/pid"
  local existing_pid=""

  while true; do
    if mkdir "${lock_dir}" 2>/dev/null; then
      printf '%s\n' "$$" > "${pid_file}"
      ACTIVE_INSTANCE_LOCK_DIRS="${ACTIVE_INSTANCE_LOCK_DIRS:-} ${lock_dir}"
      trap release_instance_locks EXIT
      return 0
    fi

    existing_pid=""
    if [[ -f "${pid_file}" ]]; then
      existing_pid="$(tr -dc '0-9' < "${pid_file}")"
    fi

    if [[ -n "${existing_pid}" ]] && kill -0 "${existing_pid}" 2>/dev/null; then
      echo "[${INDEX}] ${lock_name} already running (pid ${existing_pid})."
      return 1
    fi

    rm -rf "${lock_dir}" 2>/dev/null || true
    sleep 1
  done
}

echo "[${INDEX}] Daily folder: ${INDEX_DIR}"
echo "  - Journal:  $(basename "${JOURNAL_CSV}")"
echo "  - Signals:  $(basename "${SIGNALS_CSV}")"
echo "  - Trades:   $(basename "${TRADES_CSV}")"
echo "  - Equity:   $(basename "${EQUITY_CSV}")"
echo "  - Events:   $(basename "${EVENTS_CSV}")"
echo "  - States:   .signal_state.json, .opportunity_engine_state.json, .paper_trade_state.json"
if [[ "${MARKET_OPEN}" == "1" ]]; then
  echo "  - Market:   OPEN (9:00-15:45 IST)"
else
  echo "  - Market:   CLOSED (outside 9:00-15:45 IST or weekend)"
fi
