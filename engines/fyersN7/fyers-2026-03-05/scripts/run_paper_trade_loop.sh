#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
PAPER_SCRIPT="${ROOT_DIR}/scripts/paper_trade_loop.py"

if [[ "${PYTHON_BIN}" != */* ]]; then
  PYTHON_BIN="$(command -v "${PYTHON_BIN}" 2>/dev/null || true)"
fi

if [[ -z "${PYTHON_BIN}" ]] || [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Error: ${PYTHON_BIN} not found. Create venv first." >&2
  exit 1
fi

# Initialize daily postmortem folder
source "${ROOT_DIR}/scripts/init_daily_folder.sh"

INTERVAL_SEC="${INTERVAL_SEC:-15}"
CAPITAL="${CAPITAL:-100000}"
LOT_SIZE="${LOT_SIZE:-10}"
ENTRY_FEE="${ENTRY_FEE:-40}"
EXIT_FEE="${EXIT_FEE:-40}"
EXIT_TARGET="${EXIT_TARGET:-t1}"
MAX_HOLD_SEC="${MAX_HOLD_SEC:-180}"

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
TRAIN_MIN_LABELS="${TRAIN_MIN_LABELS:-${MIN_MODEL_SAMPLES}}"
TRAIN_LR="${TRAIN_LR:-0.15}"
TRAIN_EPOCHS="${TRAIN_EPOCHS:-600}"
AUTO_TRAIN_ON_BACKFILL="${AUTO_TRAIN_ON_BACKFILL:-1}"
CONFIRM_PULLS="${CONFIRM_PULLS:-2}"
FLIP_COOLDOWN_SEC="${FLIP_COOLDOWN_SEC:-45}"
MAX_SELECT_STRIKES="${MAX_SELECT_STRIKES:-3}"
MAX_SPREAD_PCT="${MAX_SPREAD_PCT:-2.5}"
# JOURNAL_CSV, TRADES_CSV, EQUITY_CSV, SIGNAL_STATE_FILE, PAPER_STATE_FILE set by init_daily_folder.sh
SHOW_SIGNAL_TABLE="${SHOW_SIGNAL_TABLE:-0}"

acquire_instance_lock "paper_trade_loop" || exit 0

cmd=("${PYTHON_BIN}" "${PAPER_SCRIPT}"
  --interval-sec "${INTERVAL_SEC}"
  --capital "${CAPITAL}"
  --lot-size "${LOT_SIZE}"
  --entry-fee "${ENTRY_FEE}"
  --exit-fee "${EXIT_FEE}"
  --exit-target "${EXIT_TARGET}"
  --max-hold-sec "${MAX_HOLD_SEC}"
  --profile "${PROFILE}"
  --ladder-count "${LADDER_COUNT}"
  --otm-start "${OTM_START}"
  --max-premium "${MAX_PREMIUM}"
  --min-premium "${MIN_PREMIUM}"
  --min-confidence "${MIN_CONFIDENCE}"
  --min-score "${MIN_SCORE}"
  --min-abs-delta "${MIN_ABS_DELTA}"
  --min-vote-diff "${MIN_VOTE_DIFF}"
  --adaptive-model-file "${ADAPTIVE_MODEL_FILE}"
  --min-learn-prob "${MIN_LEARN_PROB}"
  --min-model-samples "${MIN_MODEL_SAMPLES}"
  --hard-gate-min-model-samples "${HARD_GATE_MIN_MODEL_SAMPLES}"
  --learn-gate-lock-streak "${LEARN_GATE_LOCK_STREAK}"
  --learn-gate-relax-sec "${LEARN_GATE_RELAX_SEC}"
  --train-min-labels "${TRAIN_MIN_LABELS}"
  --train-lr "${TRAIN_LR}"
  --train-epochs "${TRAIN_EPOCHS}"
  --confirm-pulls "${CONFIRM_PULLS}"
  --flip-cooldown-sec "${FLIP_COOLDOWN_SEC}"
  --max-select-strikes "${MAX_SELECT_STRIKES}"
  --max-spread-pct "${MAX_SPREAD_PCT}"
  --signal-state-file "${SIGNAL_STATE_FILE}"
  --journal-csv "${JOURNAL_CSV}"
  --trades-csv "${TRADES_CSV}"
  --equity-csv "${EQUITY_CSV}"
  --paper-state-file "${PAPER_STATE_FILE}")

if [[ "${ADAPTIVE_ENABLE}" == "1" ]]; then
  cmd+=(--enable-adaptive)
else
  cmd+=(--disable-adaptive)
fi

if [[ "${SHOW_SIGNAL_TABLE}" == "1" ]]; then
  cmd+=(--show-signal-table)
fi

if [[ "${AUTO_TRAIN_ON_BACKFILL}" == "1" ]]; then
  cmd+=(--auto-train-on-backfill)
else
  cmd+=(--no-auto-train-on-backfill)
fi

# Skip pulling if signal_loop is running separately
if [[ "${NO_PULL:-0}" == "1" ]]; then
  cmd+=(--no-pull)
fi

if [[ "${SKIP_MARKET_CHECK:-0}" == "1" ]]; then
  cmd+=(--skip-market-check)
fi

"${cmd[@]}"
