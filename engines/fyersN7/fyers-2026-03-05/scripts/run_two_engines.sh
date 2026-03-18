#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIGNAL_SCRIPT="${ROOT_DIR}/scripts/run_signal_loop.sh"
OPP_SCRIPT="${ROOT_DIR}/scripts/run_opportunity_engine.sh"
PAPER_SCRIPT="${ROOT_DIR}/scripts/run_paper_trade_loop.sh"

# Enable paper trading by default (set ENABLE_PAPER_TRADING=0 to disable)
ENABLE_PAPER_TRADING="${ENABLE_PAPER_TRADING:-1}"

if [[ ! -x "${SIGNAL_SCRIPT}" ]]; then
  echo "Error: ${SIGNAL_SCRIPT} not found." >&2
  exit 1
fi
if [[ ! -x "${OPP_SCRIPT}" ]]; then
  echo "Error: ${OPP_SCRIPT} not found." >&2
  exit 1
fi
if [[ "${ENABLE_PAPER_TRADING}" == "1" && ! -x "${PAPER_SCRIPT}" ]]; then
  echo "Warning: ${PAPER_SCRIPT} not found. Paper trading disabled." >&2
  ENABLE_PAPER_TRADING=0
fi

if [[ ! -f "${ROOT_DIR}/.fyers.env" ]]; then
  echo "Error: .fyers.env not found. Please login first." >&2
  exit 1
fi

# Index to trade (SENSEX, BANKNIFTY, NIFTY, etc.)
export INDEX="${INDEX:-SENSEX}"

# Initialize daily postmortem folder for this index
source "${ROOT_DIR}/scripts/init_daily_folder.sh"

SIGNAL_INTERVAL_SEC="${INTERVAL_SEC:-15}"
SIGNAL_TRAIN_EVERY_SEC="${TRAIN_EVERY_SEC:-30}"
OPP_INTERVAL_SEC="${OPP_INTERVAL_SEC:-15}"
PAPER_INTERVAL_SEC="${PAPER_INTERVAL_SEC:-15}"

# Paper trading parameters
PAPER_CAPITAL="${CAPITAL:-5000}"
PAPER_LOT_SIZE="${LOT_SIZE:-10}"
PAPER_ENTRY_FEE="${ENTRY_FEE:-40}"
PAPER_EXIT_FEE="${EXIT_FEE:-40}"
PAPER_EXIT_TARGET="${EXIT_TARGET:-t1}"
PAPER_MAX_HOLD_SEC="${MAX_HOLD_SEC:-180}"

echo "Starting engines for ${INDEX}"
echo "1) Signal engine     : interval=${SIGNAL_INTERVAL_SEC}s train_every=${SIGNAL_TRAIN_EVERY_SEC}s"
echo "2) Opportunity engine: interval=${OPP_INTERVAL_SEC}s (NO_PULL=1, table/output only)"
if [[ "${ENABLE_PAPER_TRADING}" == "1" ]]; then
  echo "3) Paper trading     : interval=${PAPER_INTERVAL_SEC}s capital=${PAPER_CAPITAL} lot=${PAPER_LOT_SIZE}"
fi
if [[ "${MARKET_OPEN}" == "1" ]]; then
  echo "Market: OPEN - signals will be fetched"
else
  echo "Market: CLOSED - will wait until 9:00 AM IST (set SKIP_MARKET_CHECK=1 to override)"
fi
echo "Press Ctrl+C to stop all."
echo

(
  cd "${ROOT_DIR}" && \
  PYTHONUNBUFFERED=1 \
  INDEX="${INDEX}" \
  INTERVAL_SEC="${SIGNAL_INTERVAL_SEC}" \
  TRAIN_EVERY_SEC="${SIGNAL_TRAIN_EVERY_SEC}" \
  JOURNAL_CSV="${JOURNAL_CSV}" \
  STATE_FILE="${SIGNAL_STATE_FILE}" \
  SKIP_MARKET_CHECK="${SKIP_MARKET_CHECK:-0}" \
  scripts/run_signal_loop.sh 2>&1 | sed -u "s/^/[${INDEX}:SIG] /"
) &
PID_SIGNAL=$!

(
  cd "${ROOT_DIR}" && \
  PYTHONUNBUFFERED=1 \
  INDEX="${INDEX}" \
  INTERVAL_SEC="${OPP_INTERVAL_SEC}" \
  NO_PULL=1 \
  START_FROM_LATEST=1 \
  JOURNAL_CSV="${JOURNAL_CSV}" \
  EVENTS_CSV="${EVENTS_CSV}" \
  STATE_FILE="${OPP_STATE_FILE}" \
  SKIP_MARKET_CHECK="${SKIP_MARKET_CHECK:-0}" \
  scripts/run_opportunity_engine.sh 2>&1 | sed -u "s/^/[${INDEX}:OPP] /"
) &
PID_OPPORT=$!

# Start paper trading if enabled
PID_PAPER=""
if [[ "${ENABLE_PAPER_TRADING}" == "1" ]]; then
  (
    cd "${ROOT_DIR}" && \
    PYTHONUNBUFFERED=1 \
    INDEX="${INDEX}" \
    INTERVAL_SEC="${PAPER_INTERVAL_SEC}" \
    NO_PULL=1 \
    CAPITAL="${PAPER_CAPITAL}" \
    LOT_SIZE="${PAPER_LOT_SIZE}" \
    ENTRY_FEE="${PAPER_ENTRY_FEE}" \
    EXIT_FEE="${PAPER_EXIT_FEE}" \
    EXIT_TARGET="${PAPER_EXIT_TARGET}" \
    MAX_HOLD_SEC="${PAPER_MAX_HOLD_SEC}" \
    JOURNAL_CSV="${JOURNAL_CSV}" \
    TRADES_CSV="${TRADES_CSV}" \
    EQUITY_CSV="${EQUITY_CSV}" \
    PAPER_STATE_FILE="${PAPER_STATE_FILE}" \
    SIGNAL_STATE_FILE="${SIGNAL_STATE_FILE}" \
    ADAPTIVE_ENABLE="${ADAPTIVE_ENABLE:-1}" \
    MIN_LEARN_PROB="${MIN_LEARN_PROB:-0.55}" \
    MIN_MODEL_SAMPLES="${MIN_MODEL_SAMPLES:-20}" \
    SKIP_MARKET_CHECK="${SKIP_MARKET_CHECK:-0}" \
    scripts/run_paper_trade_loop.sh 2>&1 | sed -u "s/^/[${INDEX}:PAPER] /"
  ) &
  PID_PAPER=$!
fi

cleanup() {
  kill "${PID_SIGNAL}" "${PID_OPPORT}" 2>/dev/null || true
  if [[ -n "${PID_PAPER}" ]]; then
    kill "${PID_PAPER}" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

while true; do
  if ! kill -0 "${PID_SIGNAL}" 2>/dev/null; then
    break
  fi
  if ! kill -0 "${PID_OPPORT}" 2>/dev/null; then
    break
  fi
  if [[ -n "${PID_PAPER}" ]] && ! kill -0 "${PID_PAPER}" 2>/dev/null; then
    break
  fi
  sleep 1
done

cleanup
wait "${PID_SIGNAL}" 2>/dev/null || true
wait "${PID_OPPORT}" 2>/dev/null || true
if [[ -n "${PID_PAPER}" ]]; then
  wait "${PID_PAPER}" 2>/dev/null || true
fi
