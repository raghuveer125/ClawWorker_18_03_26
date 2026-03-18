#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
PULL_SCRIPT="${ROOT_DIR}/scripts/pull_fyers_signal.py"
TRAIN_SCRIPT="${ROOT_DIR}/scripts/update_adaptive_model.py"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Error: ${PYTHON_BIN} not found. Create venv first." >&2
  exit 1
fi

# Initialize daily postmortem folder
source "${ROOT_DIR}/scripts/init_daily_folder.sh"

INTERVAL_SEC="${INTERVAL_SEC:-15}"
PROFILE="${PROFILE:-expiry}"
LADDER_COUNT="${LADDER_COUNT:-5}"
OTM_START="${OTM_START:-1}"
MAX_PREMIUM="${MAX_PREMIUM:-1200}"
MIN_PREMIUM="${MIN_PREMIUM:-0}"
MIN_CONFIDENCE="${MIN_CONFIDENCE:-88}"
MIN_SCORE="${MIN_SCORE:-95}"
MIN_ABS_DELTA="${MIN_ABS_DELTA:-0.10}"
MIN_VOTE_DIFF="${MIN_VOTE_DIFF:-2}"
ADAPTIVE_ENABLE="${ADAPTIVE_ENABLE:-1}"
ADAPTIVE_MODEL_FILE="${ADAPTIVE_MODEL_FILE:-.adaptive_model.json}"
MIN_LEARN_PROB="${MIN_LEARN_PROB:-0.55}"
MIN_MODEL_SAMPLES="${MIN_MODEL_SAMPLES:-20}"
HARD_GATE_MIN_MODEL_SAMPLES="${HARD_GATE_MIN_MODEL_SAMPLES:-100}"
LEARN_GATE_LOCK_STREAK="${LEARN_GATE_LOCK_STREAK:-8}"
LEARN_GATE_RELAX_SEC="${LEARN_GATE_RELAX_SEC:-300}"
# JOURNAL_CSV, SIGNALS_CSV, SIGNAL_STATE_FILE set by init_daily_folder.sh
AUTO_TRAIN="${AUTO_TRAIN:-1}"
TRAIN_EVERY_SEC="${TRAIN_EVERY_SEC:-300}"
TRAIN_MIN_LABELS="${TRAIN_MIN_LABELS:-${MIN_MODEL_SAMPLES}}"
TRAIN_LR="${TRAIN_LR:-0.15}"
TRAIN_EPOCHS="${TRAIN_EPOCHS:-600}"
CONFIRM_PULLS="${CONFIRM_PULLS:-2}"
FLIP_COOLDOWN_SEC="${FLIP_COOLDOWN_SEC:-45}"
MAX_SELECT_STRIKES="${MAX_SELECT_STRIKES:-3}"
MAX_SPREAD_PCT="${MAX_SPREAD_PCT:-2.5}"
STATE_FILE="${SIGNAL_STATE_FILE}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

acquire_instance_lock "signal_loop" || exit 0

echo "Starting continuous signal loop"
echo "Interval: ${INTERVAL_SEC}s | Profile: ${PROFILE} | Ladder: ${LADDER_COUNT}"
echo "Filters: min_conf=${MIN_CONFIDENCE} min_score=${MIN_SCORE} min_delta=${MIN_ABS_DELTA} min_vote_diff=${MIN_VOTE_DIFF} confirm=${CONFIRM_PULLS} cooldown=${FLIP_COOLDOWN_SEC}s max_select=${MAX_SELECT_STRIKES} max_spread=${MAX_SPREAD_PCT}%"
echo "Adaptive: enable=${ADAPTIVE_ENABLE} model=${ADAPTIVE_MODEL_FILE} min_prob=${MIN_LEARN_PROB} min_samples=${MIN_MODEL_SAMPLES} hard_gate_samples=${HARD_GATE_MIN_MODEL_SAMPLES} lock_streak=${LEARN_GATE_LOCK_STREAK} relax_sec=${LEARN_GATE_RELAX_SEC}"
echo "AutoTrain: enable=${AUTO_TRAIN} every=${TRAIN_EVERY_SEC}s labels>=${TRAIN_MIN_LABELS}"
echo "Press Ctrl+C to stop."

last_train_ts=0
last_market_closed_msg=0
SKIP_MARKET_CHECK="${SKIP_MARKET_CHECK:-0}"

while true; do
  now_ts="$(date +%s)"

  # Check if market is open (skip check if SKIP_MARKET_CHECK=1)
  if [[ "${SKIP_MARKET_CHECK}" != "1" ]] && ! is_market_open; then
    # Only print message once per minute to avoid spam
    if (( now_ts - last_market_closed_msg >= 60 )); then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Market closed. Waiting..."
      last_market_closed_msg="${now_ts}"
    fi
    sleep "${INTERVAL_SEC}"
    continue
  fi

  if [[ "${AUTO_TRAIN}" == "1" ]] && (( now_ts - last_train_ts >= TRAIN_EVERY_SEC )); then
    echo
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Auto-training adaptive model..."
    "${PYTHON_BIN}" "${TRAIN_SCRIPT}" \
      --journal-csv "${JOURNAL_CSV}" \
      --model-file "${ADAPTIVE_MODEL_FILE}" \
      --min-labels "${TRAIN_MIN_LABELS}" \
      --lr "${TRAIN_LR}" \
      --epochs "${TRAIN_EPOCHS}" || true
    last_train_ts="${now_ts}"
  fi

  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pulling signals..."
  # shellcheck disable=SC2086
  cmd=("${PYTHON_BIN}" "${PULL_SCRIPT}" \
    --only-approved \
    --insecure \
    --profile "${PROFILE}" \
    --ladder-count "${LADDER_COUNT}" \
    --otm-start "${OTM_START}" \
    --max-premium "${MAX_PREMIUM}" \
    --min-premium "${MIN_PREMIUM}" \
    --min-confidence "${MIN_CONFIDENCE}" \
    --min-score "${MIN_SCORE}" \
    --min-abs-delta "${MIN_ABS_DELTA}" \
    --min-vote-diff "${MIN_VOTE_DIFF}" \
    --adaptive-model-file "${ADAPTIVE_MODEL_FILE}" \
    --min-learn-prob "${MIN_LEARN_PROB}" \
    --min-model-samples "${MIN_MODEL_SAMPLES}" \
    --hard-gate-min-model-samples "${HARD_GATE_MIN_MODEL_SAMPLES}" \
    --learn-gate-lock-streak "${LEARN_GATE_LOCK_STREAK}" \
    --learn-gate-relax-sec "${LEARN_GATE_RELAX_SEC}" \
    --journal-csv "${JOURNAL_CSV}" \
    --csv "${SIGNALS_CSV}" \
    --confirm-pulls "${CONFIRM_PULLS}" \
    --flip-cooldown-sec "${FLIP_COOLDOWN_SEC}" \
    --max-select-strikes "${MAX_SELECT_STRIKES}" \
    --max-spread-pct "${MAX_SPREAD_PCT}" \
    --state-file "${STATE_FILE}" \
    --table)

  if [[ "${ADAPTIVE_ENABLE}" == "1" ]]; then
    cmd+=(--enable-adaptive)
  fi

  if [[ -n "${EXTRA_ARGS}" ]]; then
    # shellcheck disable=SC2206
    extra=(${EXTRA_ARGS})
    cmd+=("${extra[@]}")
  fi
  "${cmd[@]}" || true

  sleep "${INTERVAL_SEC}"
done
