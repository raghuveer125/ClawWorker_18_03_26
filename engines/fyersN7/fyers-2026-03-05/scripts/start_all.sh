#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARED_ROOT="$(cd "${ROOT_DIR}/../.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
AUTH_SCRIPT="${ROOT_DIR}/scripts/fyers_auth.py"
ENGINE_SCRIPT="${ROOT_DIR}/scripts/run_opportunity_engine.sh"
SIGNAL_SCRIPT="${ROOT_DIR}/scripts/run_signal_loop.sh"
DUAL_SCRIPT="${ROOT_DIR}/scripts/run_two_engines.sh"
PAPER_SCRIPT="${ROOT_DIR}/scripts/run_paper_trade_loop.sh"
LIVE_SERVER_SCRIPT="${ROOT_DIR}/scripts/run_live_signal_server.sh"
FORENSICS_VALIDATOR_SCRIPT="${ROOT_DIR}/scripts/validate_forensics_inputs.py"
QUALITY_GATE_SCRIPT="${ROOT_DIR}/scripts/forensics_quality_gate.py"
TIMELINE_BUILD_SCRIPT="${ROOT_DIR}/scripts/forensics_reconstruct_timeline.py"
REGIME_DETECT_SCRIPT="${ROOT_DIR}/scripts/forensics_detect_regimes.py"
TRIGGER_GEN_SCRIPT="${ROOT_DIR}/scripts/forensics_generate_triggers.py"
PATTERN_TEMPLATE_SCRIPT="${ROOT_DIR}/scripts/forensics_build_pattern_templates.py"
RUN_SUMMARY_SCRIPT="${ROOT_DIR}/scripts/forensics_generate_run_summary.py"
CANARY_METRICS_SCRIPT="${ROOT_DIR}/scripts/forensics_build_canary_metrics.py"
BOT_RULES_SCRIPT="${ROOT_DIR}/scripts/forensics_generate_bot_rules_update.py"

ACTION="${1:-run}"

# Load redirect URI from shared config if available
_SHARED_REDIRECT_URI=$("${ROOT_DIR}/.venv/bin/python" -c "
import sys
sys.path.insert(0, '${ROOT_DIR}/../..')
try:
    from shared_project_engine.services import get_auth_redirect_uri
    print(get_auth_redirect_uri())
except ImportError:
    print('http://127.0.0.1:8080/')
" 2>/dev/null || echo "http://127.0.0.1:8080/")

CLIENT_ID="${FYERS_CLIENT_ID:-PZ6832VT8R-100}"
SECRET_KEY="${FYERS_SECRET_KEY:-}"
REDIRECT_URI="${FYERS_REDIRECT_URI:-${_SHARED_REDIRECT_URI}}"
INSECURE="${INSECURE:-1}"

MULTI_SCRIPT="${ROOT_DIR}/scripts/run_multi_index.sh"
MARKET_ADAPTER_HELPER="${SHARED_ROOT}/shared_project_engine/launcher/market_adapter.sh"
MARKET_ADAPTER_LOG="${SHARED_ROOT}/logs/market_adapter.log"
MARKET_ADAPTER_PID_FILE="${SHARED_ROOT}/logs/market_adapter.pid"

if [[ -f "${MARKET_ADAPTER_HELPER}" ]]; then
  # shellcheck source=/dev/null
  source "${MARKET_ADAPTER_HELPER}"
  load_market_adapter_config "${SHARED_ROOT}" "${PYTHON_BIN}"
else
  MARKET_ADAPTER_HOST="${MARKET_ADAPTER_HOST:-127.0.0.1}"
  MARKET_ADAPTER_PORT="${MARKET_ADAPTER_PORT:-8765}"
  MARKET_ADAPTER_URL="${MARKET_ADAPTER_URL:-http://${MARKET_ADAPTER_HOST}:${MARKET_ADAPTER_PORT}}"
  export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL
fi
MARKET_ADAPTER_STRICT="${MARKET_ADAPTER_STRICT:-1}"
export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL MARKET_ADAPTER_STRICT

usage() {
  cat <<'EOF'
Usage:
  scripts/start_all.sh run              # start engines + live web view (default)
  scripts/start_all.sh signal           # only signal engine (+ live web view by default)
  scripts/start_all.sh opportunity      # only opportunity engine (+ live web view by default)
  scripts/start_all.sh paper            # paper trading loop (+ live web view by default)
  scripts/start_all.sh forensics-check  # run forensics input validation now
  scripts/start_all.sh quality-check    # run forensics quality gate now
  scripts/start_all.sh timeline-build   # build canonical forensics timelines now
  scripts/start_all.sh regime-detect    # generate regime_table and turning_points now
  scripts/start_all.sh trigger-signals  # generate trigger_signals from regime outputs
  scripts/start_all.sh pattern-templates # generate pattern_templates from trigger history
  scripts/start_all.sh run-summary      # generate run_summary markdown/json outputs
  scripts/start_all.sh canary-metrics   # generate canary_metrics for rollback checks
  scripts/start_all.sh bot-rules-update # generate bot_rules_update json proposals
  scripts/start_all.sh login            # run FYERS auto login + token save
  scripts/start_all.sh login-run        # login first, then start BOTH engines + live web view
  scripts/start_all.sh multi IDX1 IDX2  # run multiple indices + live web view
  scripts/start_all.sh help

Supported indices: SENSEX, BANKNIFTY, NIFTY, FINNIFTY, MIDCPNIFTY

Optional env vars:
  INDEX                (default: SENSEX) - single index to trade
  FYERS_CLIENT_ID      (default: PZ6832VT8R-100)
  FYERS_SECRET_KEY     (required for login/login-run)
  FYERS_REDIRECT_URI   (default: http://127.0.0.1:8080/)
  INSECURE             (1/0, default: 1)
  ONCE                 (1 to run one scan cycle only, default: 0)
  ENABLE_WEB_VIEW      (1/0, default: 1) - local dashboard server toggle
  ENABLE_PAPER_TRADING (1/0, default: 1) - paper trading toggle
  LIVE_HOST            (default: 127.0.0.1)
  LIVE_PORT            (default: 8787)
  LIVE_DATE            (default: IST today)
  LIVE_INDICES         (default: inferred from action)
  LIVE_INTERVAL        (default: INTERVAL_SEC or 15)
  LIVE_POLL_INTERVAL   (default: 2)
  ENABLE_FORENSICS_VALIDATION (1/0, default: 1)
  FORENSICS_VALIDATE_ON_START (1/0, default: 0)
  FORENSICS_VALIDATE_ON_EXIT  (1/0, default: 1)
  FORENSICS_HARD_FAIL         (1/0, default: 0)  # if 1, validation failure can fail action
  FORENSICS_BASE_DIR          (default: postmortem)
  FORENSICS_SYMBOLS           (default: SENSEX,NIFTY50)
  FORENSICS_DATE              (optional YYYY-MM-DD; default latest available)
  FORENSICS_FAIL_ON_MISSING   (1/0, default: 0)  # passed to validator
  FORENSICS_REPORT_CSV        (optional report CSV path)
  FORENSICS_REPORT_JSON       (optional report JSON path)
  ENABLE_QUALITY_GATE         (1/0, default: 1)
  QUALITY_VALIDATE_ON_START   (1/0, default: 0)
  QUALITY_VALIDATE_ON_EXIT    (1/0, default: 1)
  QUALITY_HARD_FAIL           (1/0, default: 0)  # if 1, quality failure can fail action
  QUALITY_BASE_DIR            (default: FORENSICS_BASE_DIR or postmortem)
  QUALITY_SYMBOLS             (default: FORENSICS_SYMBOLS or SENSEX,NIFTY50)
  QUALITY_DATE                (optional YYYY-MM-DD; default latest available)
  QUALITY_FAIL_ON_QUALITY     (1/0, default: 0)  # passed to quality gate
  QUALITY_REPORT_CSV          (optional quality report CSV path)
  QUALITY_REPORT_JSON         (optional quality report JSON path)
  QUALITY_MAX_DECISION_DUPLICATE_RATIO (default: 0.05)
  QUALITY_MAX_SIGNALS_DUPLICATE_RATIO  (default: 0.95)
  QUALITY_MAX_DECISION_OUT_OF_ORDER_ROWS (default: 5)
  QUALITY_MAX_SIGNALS_OUT_OF_ORDER_ROWS  (default: 0)
  QUALITY_MAX_MISSING_MINUTE_RATIO       (default: 0.05)
  QUALITY_MAX_DECISION_PARSE_FAIL_RATIO  (default: 0.0)
  QUALITY_MAX_SIGNALS_PARSE_FAIL_RATIO   (default: 0.0)
  QUALITY_MAX_TAKE_INVALID_QUOTE_RATIO   (default: 0.0)
  QUALITY_MAX_TAKE_ZERO_PRICE_RATIO      (default: 0.0)
  TIMELINE_BASE_DIR            (default: FORENSICS_BASE_DIR or postmortem)
  TIMELINE_SYMBOLS             (default: FORENSICS_SYMBOLS or SENSEX,NIFTY50)
  TIMELINE_DATE                (optional YYYY-MM-DD; default latest available)
  TIMELINE_OUTPUT_DIR          (optional; default <base>/<date>)
  TIMELINE_FAIL_ON_ERRORS      (1/0, default: 0)
  REGIME_BASE_DIR              (default: TIMELINE_BASE_DIR or postmortem)
  REGIME_SYMBOLS               (default: TIMELINE_SYMBOLS or SENSEX,NIFTY50)
  REGIME_DATE                  (optional YYYY-MM-DD; default latest available)
  REGIME_TIMELINE_DIR          (optional; default <base>/<date>)
  REGIME_OUTPUT_DIR            (optional; default <base>/<date>)
  REGIME_WINDOW_MINUTES        (default: 5)
  REGIME_ATR_WINDOW            (default: 5)
  REGIME_TREND_ATR_MULT        (default: 1.2)
  REGIME_MIN_SEGMENT_MINUTES   (default: 2)
  REGIME_FAIL_ON_ERRORS        (1/0, default: 0)
  REGIME_BUILD_TIMELINE_FIRST  (1/0, default: 1)
  TRIGGER_BASE_DIR             (default: REGIME_BASE_DIR or postmortem)
  TRIGGER_SYMBOLS              (default: REGIME_SYMBOLS or SENSEX,NIFTY50)
  TRIGGER_DATE                 (optional YYYY-MM-DD; default latest available)
  TRIGGER_INPUT_DIR            (optional; default <base>/<date>)
  TRIGGER_OUTPUT_DIR           (optional; default <base>/<date>)
  TRIGGER_MIN_ACTION_SCORE     (default: 60)
  TRIGGER_MIN_OUTPUT_SCORE     (default: 0)
  TRIGGER_FAIL_ON_ERRORS       (1/0, default: 0)
  TRIGGER_BUILD_REGIME_FIRST   (1/0, default: 1)
  PATTERN_BASE_DIR             (default: TRIGGER_BASE_DIR or postmortem)
  PATTERN_SYMBOLS              (default: TRIGGER_SYMBOLS or SENSEX,NIFTY50)
  PATTERN_DATE                 (optional YYYY-MM-DD; default latest available)
  PATTERN_INPUT_DIR            (optional target date input dir; default <base>/<date>)
  PATTERN_OUTPUT_DIR           (optional; default <base>/<date>)
  PATTERN_HISTORY_DAYS         (default: 40)
  PATTERN_MIN_SAMPLE_COUNT     (default: 2)
  PATTERN_DECAY_HALF_LIFE_DAYS (default: 10)
  PATTERN_SCORE_NEUTRAL        (default: 60)
  PATTERN_FAIL_ON_ERRORS       (1/0, default: 0)
  PATTERN_BUILD_TRIGGER_FIRST  (1/0, default: 1)
  RUN_SUMMARY_BASE_DIR         (default: PATTERN_BASE_DIR or postmortem)
  RUN_SUMMARY_SYMBOLS          (default: PATTERN_SYMBOLS or SENSEX,NIFTY50)
  RUN_SUMMARY_DATE             (optional YYYY-MM-DD; default latest available)
  RUN_SUMMARY_OUTPUT_MD        (optional; default <base>/<date>/run_summary_<date>.md)
  RUN_SUMMARY_OUTPUT_JSON      (optional; default <base>/<date>/run_summary_<date>.json)
  RUN_SUMMARY_TOP_PATTERNS     (default: 8)
  RUN_SUMMARY_TOP_TRIGGERS     (default: 5)
  RUN_SUMMARY_FAIL_ON_ERRORS   (1/0, default: 0)
  RUN_SUMMARY_BUILD_PATTERN_FIRST (1/0, default: 1)
  CANARY_METRICS_BASE_DIR      (default: RUN_SUMMARY_BASE_DIR or postmortem)
  CANARY_METRICS_SYMBOLS       (default: RUN_SUMMARY_SYMBOLS or SENSEX,NIFTY50)
  CANARY_METRICS_DATE          (optional YYYY-MM-DD; default latest available)
  CANARY_METRICS_OUTPUT_JSON   (optional; default <base>/<date>/canary_metrics_<date>.json)
  CANARY_METRICS_DEFAULT_BASELINE_WIN_RATE (default: 50)
  CANARY_METRICS_MIN_REALIZED_DELTA (default: 0.01)
  CANARY_METRICS_FAIL_ON_ERRORS (1/0, default: 0)
  BOT_RULES_BASE_DIR           (default: RUN_SUMMARY_BASE_DIR or postmortem)
  BOT_RULES_SYMBOLS            (default: RUN_SUMMARY_SYMBOLS or SENSEX,NIFTY50)
  BOT_RULES_DATE               (optional YYYY-MM-DD; default latest available)
  BOT_RULES_INPUT_DIR          (optional; default <base>/<date>)
  BOT_RULES_OUTPUT_JSON        (optional; default <base>/<date>/bot_rules_update_<date>.json)
  BOT_RULES_APPROVAL_MODE      (manual|auto, default: auto)
  BOT_RULES_MAX_PROPOSALS      (default: 20)
  BOT_RULES_MIN_CONFIDENCE     (default: 60)
  BOT_RULES_MIN_SAMPLE_COUNT   (default: 8)
  BOT_RULES_MIN_HIT_RATE       (default: 85)
  BOT_RULES_MIN_EXPECTANCY     (default: 0.10)
  BOT_RULES_MIN_DECAY_SCORE    (default: 0.50)
  BOT_RULES_APPROVAL_START_DATE (optional YYYY-MM-DD; default inferred from earliest date folder)
  BOT_RULES_MANUAL_LOCK_DAYS   (default: 14)
  BOT_RULES_QUALITY_LOOKBACK_DAYS (default: 5)
  BOT_RULES_QUALITY_MIN_SCORE  (default: 95)
  BOT_RULES_GATE2_MIN_PATTERN_SAMPLES (default: 30)
  BOT_RULES_WALKFORWARD_PASS   (1/0/-1, default: -1; -1 uses auto proxy)
  BOT_RULES_WALKFORWARD_PROXY_MIN_AVG_EXPECTANCY (default: 0.12)
  BOT_RULES_WALKFORWARD_PROXY_MIN_AVG_HIT_RATE   (default: 90)
  BOT_RULES_PROJECTED_DRAWDOWN_DELTA (default: auto; auto uses proposal proxy)
  BOT_RULES_MAX_DRAWDOWN_WORSE_PCT (default: 5)
  BOT_RULES_DEPLOYMENT_MODE    (canary|paper|full, default: canary)
  BOT_RULES_CANARY_REQUIRED    (0/1, default: 1)
  BOT_RULES_ENABLE_AUTO_ROLLBACK (0/1, default: 1)
  BOT_RULES_CANARY_METRICS_JSON (optional JSON file with canary metrics)
  BOT_RULES_CANARY_CURRENT_DRAWDOWN_PCT (optional override)
  BOT_RULES_CANARY_MAX_DRAWDOWN_PCT (default: 3)
  BOT_RULES_CANARY_CURRENT_WIN_RATE_DELTA_PCT (optional override)
  BOT_RULES_CANARY_MIN_WIN_RATE_DELTA_PCT (default: -2)
  BOT_RULES_CANARY_CURRENT_ERROR_RATE_PCT (optional override)
  BOT_RULES_CANARY_MAX_ERROR_RATE_PCT (default: 2)
  BOT_RULES_CANARY_CONSECUTIVE_LOSSES (optional override)
  BOT_RULES_CANARY_MAX_CONSECUTIVE_LOSSES (default: 4)
  BOT_RULES_CANARY_OBSERVATION_TRADES (optional override)
  BOT_RULES_CANARY_MIN_OBSERVATION_TRADES (default: 8)
  BOT_RULES_ROLLBACK_HALT_DAYS (default: 2)
  BOT_RULES_FAIL_ON_ERRORS     (1/0, default: 0)
  BOT_RULES_BUILD_SUMMARY_FIRST (1/0, default: 1)
  BOT_RULES_BUILD_CANARY_METRICS_FIRST (1/0, default: 1)
  CAPITAL              (paper mode default: 5000)
  LOT_SIZE             (paper mode default: 10)
  ENTRY_FEE            (paper mode default: 40)
  EXIT_FEE             (paper mode default: 40)
  EXIT_TARGET          (paper mode default: t1)
  MAX_HOLD_SEC         (paper mode default: 180)
  TRAIN_MIN_LABELS     (paper mode default: MIN_MODEL_SAMPLES)
  AUTO_TRAIN_ON_BACKFILL (paper mode default: 1)

Examples:
  INDEX=BANKNIFTY scripts/start_all.sh run    # Trade only Bank Nifty
  scripts/start_all.sh multi SENSEX BANKNIFTY # Trade both
  ENABLE_WEB_VIEW=0 scripts/start_all.sh run  # Run engines only (no web view)
  ENABLE_PAPER_TRADING=0 scripts/start_all.sh run  # Disable paper trading
EOF
}

ensure_market_adapter() {
  local env_file="${ROOT_DIR}/.fyers.env"
  if [[ ! -f "${env_file}" ]] && [[ -f "${SHARED_ROOT}/.env" ]]; then
    env_file="${SHARED_ROOT}/.env"
  fi
  if [[ ! -f "${env_file}" ]]; then
    env_file=""
  fi

  if [[ -f "${MARKET_ADAPTER_HELPER}" ]]; then
    load_market_adapter_config "${SHARED_ROOT}" "${PYTHON_BIN}"
  fi

  if ! ensure_market_adapter_running "${SHARED_ROOT}" "${PYTHON_BIN}" "${MARKET_ADAPTER_LOG}" "${MARKET_ADAPTER_PID_FILE}" "${env_file}" "${SHARED_ROOT}"; then
    exit 1
  fi
}

LIVE_SERVER_PID=""

indices_csv() {
  local out=""
  local idx
  for idx in "$@"; do
    if [[ -z "${out}" ]]; then
      out="${idx}"
    else
      out="${out},${idx}"
    fi
  done
  echo "${out}"
}

stop_live_web() {
  if [[ -n "${LIVE_SERVER_PID}" ]]; then
    kill "${LIVE_SERVER_PID}" 2>/dev/null || true
    wait "${LIVE_SERVER_PID}" 2>/dev/null || true
    LIVE_SERVER_PID=""
  fi
}

run_forensics_validation() {
  local phase="${1:-manual}"
  if [[ "${ENABLE_FORENSICS_VALIDATION:-1}" != "1" ]]; then
    return 0
  fi

  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Warning: skip forensics validation (${phase}): ${PYTHON_BIN} not found."
    return 0
  fi
  if [[ ! -f "${FORENSICS_VALIDATOR_SCRIPT}" ]]; then
    echo "Warning: skip forensics validation (${phase}): ${FORENSICS_VALIDATOR_SCRIPT} not found."
    return 0
  fi

  local base_dir="${FORENSICS_BASE_DIR:-postmortem}"
  local symbols="${FORENSICS_SYMBOLS:-SENSEX,NIFTY50}"
  local -a cmd
  cmd=(
    "${PYTHON_BIN}" "${FORENSICS_VALIDATOR_SCRIPT}"
    --base-dir "${base_dir}"
    --symbols "${symbols}"
  )
  if [[ -n "${FORENSICS_DATE:-}" ]]; then
    cmd+=(--date "${FORENSICS_DATE}")
  fi
  if [[ -n "${FORENSICS_REPORT_CSV:-}" ]]; then
    cmd+=(--report-csv "${FORENSICS_REPORT_CSV}")
  fi
  if [[ -n "${FORENSICS_REPORT_JSON:-}" ]]; then
    cmd+=(--report-json "${FORENSICS_REPORT_JSON}")
  fi
  if [[ "${FORENSICS_FAIL_ON_MISSING:-0}" == "1" ]]; then
    cmd+=(--fail-on-missing)
  fi

  echo
  echo "Running forensics input validation (${phase})..."
  (
    cd "${ROOT_DIR}" && \
    "${cmd[@]}"
  )
}

run_quality_gate() {
  local phase="${1:-manual}"
  if [[ "${ENABLE_QUALITY_GATE:-1}" != "1" ]]; then
    return 0
  fi

  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Warning: skip quality gate (${phase}): ${PYTHON_BIN} not found."
    return 0
  fi
  if [[ ! -f "${QUALITY_GATE_SCRIPT}" ]]; then
    echo "Warning: skip quality gate (${phase}): ${QUALITY_GATE_SCRIPT} not found."
    return 0
  fi

  local base_dir="${QUALITY_BASE_DIR:-${FORENSICS_BASE_DIR:-postmortem}}"
  local symbols="${QUALITY_SYMBOLS:-${FORENSICS_SYMBOLS:-SENSEX,NIFTY50}}"
  local -a cmd
  cmd=(
    "${PYTHON_BIN}" "${QUALITY_GATE_SCRIPT}"
    --base-dir "${base_dir}"
    --symbols "${symbols}"
    --max-decision-duplicate-ratio "${QUALITY_MAX_DECISION_DUPLICATE_RATIO:-0.05}"
    --max-signals-duplicate-ratio "${QUALITY_MAX_SIGNALS_DUPLICATE_RATIO:-0.95}"
    --max-decision-out-of-order-rows "${QUALITY_MAX_DECISION_OUT_OF_ORDER_ROWS:-5}"
    --max-signals-out-of-order-rows "${QUALITY_MAX_SIGNALS_OUT_OF_ORDER_ROWS:-0}"
    --max-missing-minute-ratio "${QUALITY_MAX_MISSING_MINUTE_RATIO:-0.05}"
    --max-decision-parse-fail-ratio "${QUALITY_MAX_DECISION_PARSE_FAIL_RATIO:-0.0}"
    --max-signals-parse-fail-ratio "${QUALITY_MAX_SIGNALS_PARSE_FAIL_RATIO:-0.0}"
    --max-take-invalid-quote-ratio "${QUALITY_MAX_TAKE_INVALID_QUOTE_RATIO:-0.0}"
    --max-take-zero-price-ratio "${QUALITY_MAX_TAKE_ZERO_PRICE_RATIO:-0.0}"
  )
  if [[ -n "${QUALITY_DATE:-}" ]]; then
    cmd+=(--date "${QUALITY_DATE}")
  fi
  if [[ -n "${QUALITY_REPORT_CSV:-}" ]]; then
    cmd+=(--report-csv "${QUALITY_REPORT_CSV}")
  fi
  if [[ -n "${QUALITY_REPORT_JSON:-}" ]]; then
    cmd+=(--report-json "${QUALITY_REPORT_JSON}")
  fi
  if [[ "${QUALITY_FAIL_ON_QUALITY:-0}" == "1" ]]; then
    cmd+=(--fail-on-quality)
  fi

  echo
  echo "Running forensics quality gate (${phase})..."
  (
    cd "${ROOT_DIR}" && \
    "${cmd[@]}"
  )
}

run_timeline_build() {
  local phase="${1:-manual}"
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Warning: skip timeline build (${phase}): ${PYTHON_BIN} not found."
    return 0
  fi
  if [[ ! -f "${TIMELINE_BUILD_SCRIPT}" ]]; then
    echo "Warning: skip timeline build (${phase}): ${TIMELINE_BUILD_SCRIPT} not found."
    return 0
  fi

  local base_dir="${TIMELINE_BASE_DIR:-${FORENSICS_BASE_DIR:-postmortem}}"
  local symbols="${TIMELINE_SYMBOLS:-${FORENSICS_SYMBOLS:-SENSEX,NIFTY50}}"
  local -a cmd
  cmd=(
    "${PYTHON_BIN}" "${TIMELINE_BUILD_SCRIPT}"
    --base-dir "${base_dir}"
    --symbols "${symbols}"
  )
  if [[ -n "${TIMELINE_DATE:-}" ]]; then
    cmd+=(--date "${TIMELINE_DATE}")
  fi
  if [[ -n "${TIMELINE_OUTPUT_DIR:-}" ]]; then
    cmd+=(--output-dir "${TIMELINE_OUTPUT_DIR}")
  fi
  if [[ "${TIMELINE_FAIL_ON_ERRORS:-0}" == "1" ]]; then
    cmd+=(--fail-on-errors)
  fi

  echo
  echo "Running forensics timeline build (${phase})..."
  (
    cd "${ROOT_DIR}" && \
    "${cmd[@]}"
  )
}

run_regime_detect() {
  local phase="${1:-manual}"
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Warning: skip regime detect (${phase}): ${PYTHON_BIN} not found."
    return 0
  fi
  if [[ ! -f "${REGIME_DETECT_SCRIPT}" ]]; then
    echo "Warning: skip regime detect (${phase}): ${REGIME_DETECT_SCRIPT} not found."
    return 0
  fi

  if [[ "${REGIME_BUILD_TIMELINE_FIRST:-1}" == "1" ]]; then
    run_timeline_build "${phase}" || true
  fi

  local base_dir="${REGIME_BASE_DIR:-${TIMELINE_BASE_DIR:-${FORENSICS_BASE_DIR:-postmortem}}}"
  local symbols="${REGIME_SYMBOLS:-${TIMELINE_SYMBOLS:-${FORENSICS_SYMBOLS:-SENSEX,NIFTY50}}}"
  local -a cmd
  cmd=(
    "${PYTHON_BIN}" "${REGIME_DETECT_SCRIPT}"
    --base-dir "${base_dir}"
    --symbols "${symbols}"
    --window-minutes "${REGIME_WINDOW_MINUTES:-5}"
    --atr-window "${REGIME_ATR_WINDOW:-5}"
    --trend-atr-mult "${REGIME_TREND_ATR_MULT:-1.2}"
    --min-segment-minutes "${REGIME_MIN_SEGMENT_MINUTES:-2}"
  )
  if [[ -n "${REGIME_DATE:-}" ]]; then
    cmd+=(--date "${REGIME_DATE}")
  fi
  if [[ -n "${REGIME_TIMELINE_DIR:-}" ]]; then
    cmd+=(--timeline-dir "${REGIME_TIMELINE_DIR}")
  fi
  if [[ -n "${REGIME_OUTPUT_DIR:-}" ]]; then
    cmd+=(--output-dir "${REGIME_OUTPUT_DIR}")
  fi
  if [[ "${REGIME_FAIL_ON_ERRORS:-0}" == "1" ]]; then
    cmd+=(--fail-on-errors)
  fi

  echo
  echo "Running regime detection (${phase})..."
  (
    cd "${ROOT_DIR}" && \
    "${cmd[@]}"
  )
}

run_trigger_signals() {
  local phase="${1:-manual}"
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Warning: skip trigger generation (${phase}): ${PYTHON_BIN} not found."
    return 0
  fi
  if [[ ! -f "${TRIGGER_GEN_SCRIPT}" ]]; then
    echo "Warning: skip trigger generation (${phase}): ${TRIGGER_GEN_SCRIPT} not found."
    return 0
  fi

  if [[ "${TRIGGER_BUILD_REGIME_FIRST:-1}" == "1" ]]; then
    run_regime_detect "${phase}" || true
  fi

  local base_dir="${TRIGGER_BASE_DIR:-${REGIME_BASE_DIR:-${TIMELINE_BASE_DIR:-${FORENSICS_BASE_DIR:-postmortem}}}}"
  local symbols="${TRIGGER_SYMBOLS:-${REGIME_SYMBOLS:-${TIMELINE_SYMBOLS:-${FORENSICS_SYMBOLS:-SENSEX,NIFTY50}}}}"
  local -a cmd
  cmd=(
    "${PYTHON_BIN}" "${TRIGGER_GEN_SCRIPT}"
    --base-dir "${base_dir}"
    --symbols "${symbols}"
    --min-action-score "${TRIGGER_MIN_ACTION_SCORE:-60}"
    --min-output-score "${TRIGGER_MIN_OUTPUT_SCORE:-0}"
  )
  if [[ -n "${TRIGGER_DATE:-}" ]]; then
    cmd+=(--date "${TRIGGER_DATE}")
  fi
  if [[ -n "${TRIGGER_INPUT_DIR:-}" ]]; then
    cmd+=(--input-dir "${TRIGGER_INPUT_DIR}")
  fi
  if [[ -n "${TRIGGER_OUTPUT_DIR:-}" ]]; then
    cmd+=(--output-dir "${TRIGGER_OUTPUT_DIR}")
  fi
  if [[ "${TRIGGER_FAIL_ON_ERRORS:-0}" == "1" ]]; then
    cmd+=(--fail-on-errors)
  fi

  echo
  echo "Running trigger signal generation (${phase})..."
  (
    cd "${ROOT_DIR}" && \
    "${cmd[@]}"
  )
}

run_pattern_templates() {
  local phase="${1:-manual}"
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Warning: skip pattern templates (${phase}): ${PYTHON_BIN} not found."
    return 0
  fi
  if [[ ! -f "${PATTERN_TEMPLATE_SCRIPT}" ]]; then
    echo "Warning: skip pattern templates (${phase}): ${PATTERN_TEMPLATE_SCRIPT} not found."
    return 0
  fi

  if [[ "${PATTERN_BUILD_TRIGGER_FIRST:-1}" == "1" ]]; then
    run_trigger_signals "${phase}" || true
  fi

  local base_dir="${PATTERN_BASE_DIR:-${TRIGGER_BASE_DIR:-${REGIME_BASE_DIR:-${TIMELINE_BASE_DIR:-${FORENSICS_BASE_DIR:-postmortem}}}}}"
  local symbols="${PATTERN_SYMBOLS:-${TRIGGER_SYMBOLS:-${REGIME_SYMBOLS:-${TIMELINE_SYMBOLS:-${FORENSICS_SYMBOLS:-SENSEX,NIFTY50}}}}}"
  local -a cmd
  cmd=(
    "${PYTHON_BIN}" "${PATTERN_TEMPLATE_SCRIPT}"
    --base-dir "${base_dir}"
    --symbols "${symbols}"
    --history-days "${PATTERN_HISTORY_DAYS:-40}"
    --min-sample-count "${PATTERN_MIN_SAMPLE_COUNT:-2}"
    --decay-half-life-days "${PATTERN_DECAY_HALF_LIFE_DAYS:-10}"
    --score-neutral "${PATTERN_SCORE_NEUTRAL:-60}"
  )
  if [[ -n "${PATTERN_DATE:-}" ]]; then
    cmd+=(--date "${PATTERN_DATE}")
  fi
  if [[ -n "${PATTERN_INPUT_DIR:-}" ]]; then
    cmd+=(--input-dir "${PATTERN_INPUT_DIR}")
  fi
  if [[ -n "${PATTERN_OUTPUT_DIR:-}" ]]; then
    cmd+=(--output-dir "${PATTERN_OUTPUT_DIR}")
  fi
  if [[ "${PATTERN_FAIL_ON_ERRORS:-0}" == "1" ]]; then
    cmd+=(--fail-on-errors)
  fi

  echo
  echo "Running pattern template generation (${phase})..."
  (
    cd "${ROOT_DIR}" && \
    "${cmd[@]}"
  )
}

run_run_summary() {
  local phase="${1:-manual}"
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Warning: skip run summary (${phase}): ${PYTHON_BIN} not found."
    return 0
  fi
  if [[ ! -f "${RUN_SUMMARY_SCRIPT}" ]]; then
    echo "Warning: skip run summary (${phase}): ${RUN_SUMMARY_SCRIPT} not found."
    return 0
  fi

  if [[ "${RUN_SUMMARY_BUILD_PATTERN_FIRST:-1}" == "1" ]]; then
    run_pattern_templates "${phase}" || true
  fi

  local base_dir="${RUN_SUMMARY_BASE_DIR:-${PATTERN_BASE_DIR:-${TRIGGER_BASE_DIR:-${REGIME_BASE_DIR:-${TIMELINE_BASE_DIR:-${FORENSICS_BASE_DIR:-postmortem}}}}}}"
  local symbols="${RUN_SUMMARY_SYMBOLS:-${PATTERN_SYMBOLS:-${TRIGGER_SYMBOLS:-${REGIME_SYMBOLS:-${TIMELINE_SYMBOLS:-${FORENSICS_SYMBOLS:-SENSEX,NIFTY50}}}}}}"
  local -a cmd
  cmd=(
    "${PYTHON_BIN}" "${RUN_SUMMARY_SCRIPT}"
    --base-dir "${base_dir}"
    --symbols "${symbols}"
    --top-patterns "${RUN_SUMMARY_TOP_PATTERNS:-8}"
    --top-triggers "${RUN_SUMMARY_TOP_TRIGGERS:-5}"
  )
  if [[ -n "${RUN_SUMMARY_DATE:-}" ]]; then
    cmd+=(--date "${RUN_SUMMARY_DATE}")
  fi
  if [[ -n "${RUN_SUMMARY_OUTPUT_MD:-}" ]]; then
    cmd+=(--output-md "${RUN_SUMMARY_OUTPUT_MD}")
  fi
  if [[ -n "${RUN_SUMMARY_OUTPUT_JSON:-}" ]]; then
    cmd+=(--output-json "${RUN_SUMMARY_OUTPUT_JSON}")
  fi
  if [[ "${RUN_SUMMARY_FAIL_ON_ERRORS:-0}" == "1" ]]; then
    cmd+=(--fail-on-errors)
  fi

  echo
  echo "Running forensics run summary generation (${phase})..."
  (
    cd "${ROOT_DIR}" && \
    "${cmd[@]}"
  )
}

run_canary_metrics() {
  local phase="${1:-manual}"
  local base_override="${2:-}"
  local symbols_override="${3:-}"
  local date_override="${4:-}"

  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Warning: skip canary metrics (${phase}): ${PYTHON_BIN} not found."
    return 0
  fi
  if [[ ! -f "${CANARY_METRICS_SCRIPT}" ]]; then
    echo "Warning: skip canary metrics (${phase}): ${CANARY_METRICS_SCRIPT} not found."
    return 0
  fi

  local base_dir="${base_override:-${CANARY_METRICS_BASE_DIR:-${RUN_SUMMARY_BASE_DIR:-${PATTERN_BASE_DIR:-${TRIGGER_BASE_DIR:-${REGIME_BASE_DIR:-${TIMELINE_BASE_DIR:-${FORENSICS_BASE_DIR:-postmortem}}}}}}}}"
  local symbols="${symbols_override:-${CANARY_METRICS_SYMBOLS:-${RUN_SUMMARY_SYMBOLS:-${PATTERN_SYMBOLS:-${TRIGGER_SYMBOLS:-${REGIME_SYMBOLS:-${TIMELINE_SYMBOLS:-${FORENSICS_SYMBOLS:-SENSEX,NIFTY50}}}}}}}}"
  local run_date="${date_override:-${CANARY_METRICS_DATE:-}}"
  local -a cmd
  cmd=(
    "${PYTHON_BIN}" "${CANARY_METRICS_SCRIPT}"
    --base-dir "${base_dir}"
    --symbols "${symbols}"
    --default-baseline-win-rate "${CANARY_METRICS_DEFAULT_BASELINE_WIN_RATE:-50}"
    --min-realized-delta "${CANARY_METRICS_MIN_REALIZED_DELTA:-0.01}"
  )
  if [[ -n "${run_date}" ]]; then
    cmd+=(--date "${run_date}")
  fi
  if [[ -n "${CANARY_METRICS_OUTPUT_JSON:-}" ]]; then
    cmd+=(--output-json "${CANARY_METRICS_OUTPUT_JSON}")
  fi
  if [[ "${CANARY_METRICS_FAIL_ON_ERRORS:-0}" == "1" ]]; then
    cmd+=(--fail-on-errors)
  fi

  echo
  echo "Running canary metrics build (${phase})..."
  (
    cd "${ROOT_DIR}" && \
    "${cmd[@]}"
  )
}

run_bot_rules_update() {
  local phase="${1:-manual}"
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Warning: skip bot rules update (${phase}): ${PYTHON_BIN} not found."
    return 0
  fi
  if [[ ! -f "${BOT_RULES_SCRIPT}" ]]; then
    echo "Warning: skip bot rules update (${phase}): ${BOT_RULES_SCRIPT} not found."
    return 0
  fi

  if [[ "${BOT_RULES_BUILD_SUMMARY_FIRST:-1}" == "1" ]]; then
    run_run_summary "${phase}" || true
  fi

  local base_dir="${BOT_RULES_BASE_DIR:-${RUN_SUMMARY_BASE_DIR:-${PATTERN_BASE_DIR:-${TRIGGER_BASE_DIR:-${REGIME_BASE_DIR:-${TIMELINE_BASE_DIR:-${FORENSICS_BASE_DIR:-postmortem}}}}}}}"
  local symbols="${BOT_RULES_SYMBOLS:-${RUN_SUMMARY_SYMBOLS:-${PATTERN_SYMBOLS:-${TRIGGER_SYMBOLS:-${REGIME_SYMBOLS:-${TIMELINE_SYMBOLS:-${FORENSICS_SYMBOLS:-SENSEX,NIFTY50}}}}}}}"
  local canary_base_dir="${CANARY_METRICS_BASE_DIR:-${base_dir}}"
  local canary_symbols="${CANARY_METRICS_SYMBOLS:-${symbols}}"
  local canary_date="${CANARY_METRICS_DATE:-${BOT_RULES_DATE:-}}"

  if [[ "${BOT_RULES_BUILD_CANARY_METRICS_FIRST:-1}" == "1" ]]; then
    run_canary_metrics "${phase}" "${canary_base_dir}" "${canary_symbols}" "${canary_date}" || true
  fi

  local -a cmd
  cmd=(
    "${PYTHON_BIN}" "${BOT_RULES_SCRIPT}"
    --base-dir "${base_dir}"
    --symbols "${symbols}"
    --approval-mode "${BOT_RULES_APPROVAL_MODE:-auto}"
    --max-proposals "${BOT_RULES_MAX_PROPOSALS:-20}"
    --min-confidence "${BOT_RULES_MIN_CONFIDENCE:-60}"
    --min-sample-count "${BOT_RULES_MIN_SAMPLE_COUNT:-8}"
    --min-hit-rate "${BOT_RULES_MIN_HIT_RATE:-85}"
    --min-expectancy "${BOT_RULES_MIN_EXPECTANCY:-0.10}"
    --min-decay-score "${BOT_RULES_MIN_DECAY_SCORE:-0.50}"
    --manual-lock-days "${BOT_RULES_MANUAL_LOCK_DAYS:-14}"
    --quality-lookback-days "${BOT_RULES_QUALITY_LOOKBACK_DAYS:-5}"
    --quality-min-score "${BOT_RULES_QUALITY_MIN_SCORE:-95}"
    --gate2-min-pattern-samples "${BOT_RULES_GATE2_MIN_PATTERN_SAMPLES:-30}"
    --walkforward-pass "${BOT_RULES_WALKFORWARD_PASS:--1}"
    --walkforward-proxy-min-avg-expectancy "${BOT_RULES_WALKFORWARD_PROXY_MIN_AVG_EXPECTANCY:-0.12}"
    --walkforward-proxy-min-avg-hit-rate "${BOT_RULES_WALKFORWARD_PROXY_MIN_AVG_HIT_RATE:-90}"
    --projected-drawdown-delta "${BOT_RULES_PROJECTED_DRAWDOWN_DELTA:-auto}"
    --max-drawdown-worse-pct "${BOT_RULES_MAX_DRAWDOWN_WORSE_PCT:-5}"
    --deployment-mode "${BOT_RULES_DEPLOYMENT_MODE:-canary}"
    --canary-required "${BOT_RULES_CANARY_REQUIRED:-1}"
    --enable-auto-rollback "${BOT_RULES_ENABLE_AUTO_ROLLBACK:-1}"
    --canary-max-drawdown-pct "${BOT_RULES_CANARY_MAX_DRAWDOWN_PCT:-3}"
    --canary-min-win-rate-delta-pct "${BOT_RULES_CANARY_MIN_WIN_RATE_DELTA_PCT:--2}"
    --canary-max-error-rate-pct "${BOT_RULES_CANARY_MAX_ERROR_RATE_PCT:-2}"
    --canary-max-consecutive-losses "${BOT_RULES_CANARY_MAX_CONSECUTIVE_LOSSES:-4}"
    --canary-min-observation-trades "${BOT_RULES_CANARY_MIN_OBSERVATION_TRADES:-8}"
    --rollback-halt-days "${BOT_RULES_ROLLBACK_HALT_DAYS:-2}"
  )
  if [[ -n "${BOT_RULES_DATE:-}" ]]; then
    cmd+=(--date "${BOT_RULES_DATE}")
  fi
  if [[ -n "${BOT_RULES_INPUT_DIR:-}" ]]; then
    cmd+=(--input-dir "${BOT_RULES_INPUT_DIR}")
  fi
  if [[ -n "${BOT_RULES_OUTPUT_JSON:-}" ]]; then
    cmd+=(--output-json "${BOT_RULES_OUTPUT_JSON}")
  fi
  if [[ -n "${BOT_RULES_APPROVAL_START_DATE:-}" ]]; then
    cmd+=(--approval-start-date "${BOT_RULES_APPROVAL_START_DATE}")
  fi
  if [[ -n "${BOT_RULES_CANARY_METRICS_JSON:-}" ]]; then
    cmd+=(--canary-metrics-json "${BOT_RULES_CANARY_METRICS_JSON}")
  elif [[ -n "${CANARY_METRICS_OUTPUT_JSON:-}" ]]; then
    cmd+=(--canary-metrics-json "${CANARY_METRICS_OUTPUT_JSON}")
  fi
  if [[ -n "${BOT_RULES_CANARY_CURRENT_DRAWDOWN_PCT:-}" ]]; then
    cmd+=(--canary-current-drawdown-pct "${BOT_RULES_CANARY_CURRENT_DRAWDOWN_PCT}")
  fi
  if [[ -n "${BOT_RULES_CANARY_CURRENT_WIN_RATE_DELTA_PCT:-}" ]]; then
    cmd+=(--canary-current-win-rate-delta-pct "${BOT_RULES_CANARY_CURRENT_WIN_RATE_DELTA_PCT}")
  fi
  if [[ -n "${BOT_RULES_CANARY_CURRENT_ERROR_RATE_PCT:-}" ]]; then
    cmd+=(--canary-current-error-rate-pct "${BOT_RULES_CANARY_CURRENT_ERROR_RATE_PCT}")
  fi
  if [[ -n "${BOT_RULES_CANARY_CONSECUTIVE_LOSSES:-}" ]]; then
    cmd+=(--canary-consecutive-losses "${BOT_RULES_CANARY_CONSECUTIVE_LOSSES}")
  fi
  if [[ -n "${BOT_RULES_CANARY_OBSERVATION_TRADES:-}" ]]; then
    cmd+=(--canary-observation-trades "${BOT_RULES_CANARY_OBSERVATION_TRADES}")
  fi
  if [[ "${BOT_RULES_FAIL_ON_ERRORS:-0}" == "1" ]]; then
    cmd+=(--fail-on-errors)
  fi

  echo
  echo "Running bot rules update generation (${phase})..."
  (
    cd "${ROOT_DIR}" && \
    "${cmd[@]}"
  )
}

start_live_web() {
  if [[ "${ENABLE_WEB_VIEW:-1}" != "1" ]]; then
    return
  fi

  if [[ ! -f "${LIVE_SERVER_SCRIPT}" ]]; then
    echo "Warning: live web script not found at ${LIVE_SERVER_SCRIPT}; continuing without web view."
    return
  fi

  local live_date="${LIVE_DATE:-$(TZ=Asia/Kolkata date +%F)}"
  local live_host="${LIVE_HOST:-127.0.0.1}"
  local live_port="${LIVE_PORT:-8787}"
  local live_interval="${LIVE_INTERVAL:-${INTERVAL_SEC:-15}}"
  local live_poll="${LIVE_POLL_INTERVAL:-2}"
  local live_indices="${LIVE_INDICES:-SENSEX,NIFTY50,BANKNIFTY,FINNIFTY}"

  echo "Starting live web view at http://${live_host}:${live_port}"
  (
    cd "${ROOT_DIR}" && \
    HOST="${live_host}" \
    PORT="${live_port}" \
    INTERVAL="${live_interval}" \
    POLL_INTERVAL="${live_poll}" \
    INDICES="${live_indices}" \
    bash "${LIVE_SERVER_SCRIPT}" "${live_date}"
  ) &
  LIVE_SERVER_PID=$!

  sleep 1
  if ! kill -0 "${LIVE_SERVER_PID}" 2>/dev/null; then
    echo "Warning: live web server failed to start; continuing without it."
    LIVE_SERVER_PID=""
    return
  fi
}

run_with_optional_web() {
  local target_fn="$1"
  shift
  local validation_rc=0
  local quality_rc=0

  trap stop_live_web EXIT INT TERM
  start_live_web

  if [[ "${FORENSICS_VALIDATE_ON_START:-0}" == "1" ]]; then
    if ! run_forensics_validation "start"; then
      validation_rc=1
      echo "Warning: forensics validation (start) reported failure."
      if [[ "${FORENSICS_HARD_FAIL:-0}" == "1" ]]; then
        stop_live_web
        trap - EXIT INT TERM
        return 1
      fi
    fi
  fi

  if [[ "${QUALITY_VALIDATE_ON_START:-0}" == "1" ]]; then
    if ! run_quality_gate "start"; then
      quality_rc=1
      echo "Warning: quality gate (start) reported failure."
      if [[ "${QUALITY_HARD_FAIL:-0}" == "1" ]]; then
        stop_live_web
        trap - EXIT INT TERM
        return 1
      fi
    fi
  fi

  "${target_fn}" "$@"
  local rc=$?

  if [[ "${FORENSICS_VALIDATE_ON_EXIT:-1}" == "1" ]]; then
    if ! run_forensics_validation "exit"; then
      validation_rc=1
      echo "Warning: forensics validation (exit) reported failure."
    fi
  fi

  if [[ "${QUALITY_VALIDATE_ON_EXIT:-1}" == "1" ]]; then
    if ! run_quality_gate "exit"; then
      quality_rc=1
      echo "Warning: quality gate (exit) reported failure."
    fi
  fi

  stop_live_web
  trap - EXIT INT TERM

  if [[ "${FORENSICS_HARD_FAIL:-0}" == "1" && "${validation_rc}" -ne 0 && "${rc}" -eq 0 ]]; then
    rc=1
  fi
  if [[ "${QUALITY_HARD_FAIL:-0}" == "1" && "${quality_rc}" -ne 0 && "${rc}" -eq 0 ]]; then
    rc=1
  fi
  return "${rc}"
}

ensure_env() {
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Error: ${PYTHON_BIN} not found. Create venv first." >&2
    exit 1
  fi
}

run_login() {
  ensure_env
  if [[ -z "${SECRET_KEY}" ]]; then
    echo "Error: set FYERS_SECRET_KEY before login." >&2
    echo "Example:" >&2
    echo "  FYERS_SECRET_KEY='your_secret' scripts/start_all.sh login" >&2
    exit 1
  fi

  cmd=(
    "${PYTHON_BIN}" "${AUTH_SCRIPT}"
    --client-id "${CLIENT_ID}"
    --secret-key "${SECRET_KEY}"
    --redirect-uri "${REDIRECT_URI}"
    --auto-callback
  )
  if [[ "${INSECURE}" == "1" ]]; then
    cmd+=(--insecure)
  fi

  echo "Starting FYERS login..."
  (cd "${ROOT_DIR}" && "${cmd[@]}")

  if [[ ! -f "${ROOT_DIR}/.fyers.env" ]]; then
    echo "Error: .fyers.env not created." >&2
    exit 1
  fi
  if ! grep -q '^FYERS_ACCESS_TOKEN=' "${ROOT_DIR}/.fyers.env"; then
    echo "Error: FYERS_ACCESS_TOKEN missing in .fyers.env" >&2
    exit 1
  fi
  echo "Login complete. Token saved in .fyers.env"
}

run_engine() {
  ensure_env
  ensure_market_adapter
  if [[ ! -f "${ROOT_DIR}/.fyers.env" ]]; then
    echo "Error: .fyers.env not found. Run login first." >&2
    echo "  scripts/start_all.sh login" >&2
    exit 1
  fi

  extra_args="${EXTRA_ARGS:-}"
  if [[ "${ONCE:-0}" == "1" ]]; then
    if [[ -n "${extra_args}" ]]; then
      extra_args="${extra_args} --once"
    else
      extra_args="--once"
    fi
  fi

  echo "Starting opportunity engine with recommended profile..."
  echo "Interval: ${INTERVAL_SEC:-15}s | Profile: ${PROFILE:-expiry} | Ladder: ${LADDER_COUNT:-6}"
  echo "Entry filters: score>=${MIN_ENTRY_SCORE:-74} conf>=${MIN_CONF_ENTRY:-84} vote>=${MIN_VOTE_DIFF_ENTRY:-5} spread<=${MAX_SPREAD_ENTRY:-2.2}%"
  echo

  (
    cd "${ROOT_DIR}" && \
    INDEX="${INDEX:-SENSEX}" \
    INTERVAL_SEC="${INTERVAL_SEC:-15}" \
    PROFILE="${PROFILE:-expiry}" \
    LADDER_COUNT="${LADDER_COUNT:-6}" \
    OTM_START="${OTM_START:-1}" \
    MAX_PREMIUM="${MAX_PREMIUM:-1200}" \
    MIN_PREMIUM="${MIN_PREMIUM:-0}" \
    ADAPTIVE_ENABLE="${ADAPTIVE_ENABLE:-1}" \
    MIN_LEARN_PROB="${MIN_LEARN_PROB:-0.55}" \
    MIN_MODEL_SAMPLES="${MIN_MODEL_SAMPLES:-20}" \
    MAX_SELECT_STRIKES="${MAX_SELECT_STRIKES:-4}" \
    MAX_SPREAD_PCT="${MAX_SPREAD_PCT:-2.5}" \
    MIN_ENTRY_SCORE="${MIN_ENTRY_SCORE:-74}" \
    MIN_CONF_ENTRY="${MIN_CONF_ENTRY:-84}" \
    MIN_VOTE_DIFF_ENTRY="${MIN_VOTE_DIFF_ENTRY:-5}" \
    MAX_SPREAD_ENTRY="${MAX_SPREAD_ENTRY:-2.2}" \
    MAX_HOLD_SEC="${MAX_HOLD_SEC:-180}" \
    EXIT_ON_FLIP="${EXIT_ON_FLIP:-1}" \
    FLIP_VOTE_DIFF="${FLIP_VOTE_DIFF:-5}" \
    START_FROM_LATEST="${START_FROM_LATEST:-1}" \
    EXTRA_ARGS="${extra_args}" \
    "${ENGINE_SCRIPT}"
  )
}

run_signal() {
  ensure_env
  ensure_market_adapter
  if [[ ! -f "${ROOT_DIR}/.fyers.env" ]]; then
    echo "Error: .fyers.env not found. Run login first." >&2
    echo "  scripts/start_all.sh login" >&2
    exit 1
  fi
  (
    cd "${ROOT_DIR}" && \
    INDEX="${INDEX:-SENSEX}" \
    INTERVAL_SEC="${INTERVAL_SEC:-15}" \
    TRAIN_EVERY_SEC="${TRAIN_EVERY_SEC:-30}" \
    "${SIGNAL_SCRIPT}"
  )
}

run_paper() {
  ensure_env
  ensure_market_adapter
  if [[ ! -f "${ROOT_DIR}/.fyers.env" ]]; then
    echo "Error: .fyers.env not found. Run login first." >&2
    echo "  scripts/start_all.sh login" >&2
    exit 1
  fi

  (
    cd "${ROOT_DIR}" && \
    INDEX="${INDEX:-SENSEX}" \
    INTERVAL_SEC="${INTERVAL_SEC:-15}" \
    CAPITAL="${CAPITAL:-5000}" \
    LOT_SIZE="${LOT_SIZE:-10}" \
    ENTRY_FEE="${ENTRY_FEE:-40}" \
    EXIT_FEE="${EXIT_FEE:-40}" \
    EXIT_TARGET="${EXIT_TARGET:-t1}" \
    MAX_HOLD_SEC="${MAX_HOLD_SEC:-180}" \
    ADAPTIVE_ENABLE="${ADAPTIVE_ENABLE:-1}" \
    MIN_LEARN_PROB="${MIN_LEARN_PROB:-0.55}" \
    MIN_MODEL_SAMPLES="${MIN_MODEL_SAMPLES:-20}" \
    TRAIN_MIN_LABELS="${TRAIN_MIN_LABELS:-${MIN_MODEL_SAMPLES:-20}}" \
    TRAIN_LR="${TRAIN_LR:-0.15}" \
    TRAIN_EPOCHS="${TRAIN_EPOCHS:-600}" \
    AUTO_TRAIN_ON_BACKFILL="${AUTO_TRAIN_ON_BACKFILL:-1}" \
    "${PAPER_SCRIPT}"
  )
}

run_dual() {
  ensure_env
  ensure_market_adapter
  if [[ ! -f "${ROOT_DIR}/.fyers.env" ]]; then
    echo "Error: .fyers.env not found. Run login first." >&2
    echo "  scripts/start_all.sh login" >&2
    exit 1
  fi
  (
    cd "${ROOT_DIR}" && \
    INDEX="${INDEX:-SENSEX}" \
    INTERVAL_SEC="${INTERVAL_SEC:-15}" \
    TRAIN_EVERY_SEC="${TRAIN_EVERY_SEC:-30}" \
    OPP_INTERVAL_SEC="${OPP_INTERVAL_SEC:-15}" \
    "${DUAL_SCRIPT}"
  )
}

run_multi() {
  ensure_env
  ensure_market_adapter
  if [[ ! -f "${ROOT_DIR}/.fyers.env" ]]; then
    echo "Error: .fyers.env not found. Run login first." >&2
    echo "  scripts/start_all.sh login" >&2
    exit 1
  fi
  (
    cd "${ROOT_DIR}" && \
    INTERVAL_SEC="${INTERVAL_SEC:-15}" \
    OPP_INTERVAL_SEC="${OPP_INTERVAL_SEC:-15}" \
    "${MULTI_SCRIPT}" "$@"
  )
}

run_forensics_check() {
  ensure_env
  run_forensics_validation "manual"
}

run_quality_check() {
  ensure_env
  run_quality_gate "manual"
}

run_timeline_check() {
  ensure_env
  run_timeline_build "manual"
}

run_regime_check() {
  ensure_env
  run_regime_detect "manual"
}

run_trigger_check() {
  ensure_env
  run_trigger_signals "manual"
}

run_pattern_check() {
  ensure_env
  run_pattern_templates "manual"
}

run_summary_check() {
  ensure_env
  run_run_summary "manual"
}

run_canary_metrics_check() {
  ensure_env
  run_canary_metrics "manual"
}

run_bot_rules_check() {
  ensure_env
  run_bot_rules_update "manual"
}

case "${ACTION}" in
  run)
    if [[ -n "${INDEX:-}" ]]; then
      if [[ -z "${LIVE_INDICES:-}" ]]; then
        LIVE_INDICES="${INDEX}"
      fi
      run_with_optional_web run_dual
    else
      # Get indices from shared config (uses run_multi_index.sh which reads from shared_project_engine)
      if [[ -z "${LIVE_INDICES:-}" ]]; then
        LIVE_INDICES="SENSEX,NIFTY50,BANKNIFTY,FINNIFTY"
      fi
      # run_multi with no args uses ACTIVE_INDICES from shared_project_engine
      run_with_optional_web run_multi
    fi
    ;;
  signal)
    if [[ -z "${LIVE_INDICES:-}" ]]; then
      LIVE_INDICES="${INDEX:-SENSEX}"
    fi
    run_with_optional_web run_signal
    ;;
  opportunity)
    if [[ -z "${LIVE_INDICES:-}" ]]; then
      LIVE_INDICES="${INDEX:-SENSEX}"
    fi
    run_with_optional_web run_engine
    ;;
  paper)
    if [[ -z "${LIVE_INDICES:-}" ]]; then
      LIVE_INDICES="${INDEX:-SENSEX}"
    fi
    run_with_optional_web run_paper
    ;;
  forensics-check)
    run_forensics_check
    ;;
  quality-check)
    run_quality_check
    ;;
  timeline-build)
    run_timeline_check
    ;;
  regime-detect)
    run_regime_check
    ;;
  trigger-signals)
    run_trigger_check
    ;;
  pattern-templates)
    run_pattern_check
    ;;
  run-summary)
    run_summary_check
    ;;
  canary-metrics)
    run_canary_metrics_check
    ;;
  bot-rules-update)
    run_bot_rules_check
    ;;
  login)
    run_login
    ;;
  login-run)
    run_login
    if [[ -z "${LIVE_INDICES:-}" ]]; then
      LIVE_INDICES="${INDEX:-SENSEX}"
    fi
    run_with_optional_web run_dual
    ;;
  multi)
    shift  # Remove 'multi' from $@
    if [[ -z "${LIVE_INDICES:-}" ]]; then
      if [[ "$#" -gt 0 ]]; then
        LIVE_INDICES="$(indices_csv "$@")"
      else
        LIVE_INDICES="SENSEX,NIFTY50,BANKNIFTY,FINNIFTY"
      fi
    fi
    run_with_optional_web run_multi "$@"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown action: ${ACTION}" >&2
    usage
    exit 1
    ;;
esac
