#!/usr/bin/env bash
# OPTIMIZED PAPER TRADING CONFIGURATION
# Based on deep analysis of failed trades - March 2026
#
# Key changes from default:
# 1. MIN_PREMIUM=50 - Avoid low premium options (<50 had 24% win rate)
# 2. MAX_SELECT_STRIKES=1 - Reduce trade frequency (fees were 167% of gross P&L)
# 3. MIN_CONFIDENCE=92 - Tighter entry criteria
# 4. MIN_SCORE=98 - Only highest quality signals
# 5. CONFIRM_PULLS=3 - More confirmation before entry
# 6. MIN_VOTE_DIFF=4 - Stronger trend signal required
# 7. MAX_HOLD_SEC=300 - Give trades more room to work

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

# OPTIMIZED PARAMETERS
INTERVAL_SEC="${INTERVAL_SEC:-15}"
CAPITAL="${CAPITAL:-5000}"
LOT_SIZE="${LOT_SIZE:-10}"
ENTRY_FEE="${ENTRY_FEE:-40}"
EXIT_FEE="${EXIT_FEE:-40}"
EXIT_TARGET="${EXIT_TARGET:-t1}"

# Key change: 300s hold instead of 180s - let trades work
MAX_HOLD_SEC="${MAX_HOLD_SEC:-300}"

PROFILE="${PROFILE:-expiry}"
LADDER_COUNT="${LADDER_COUNT:-5}"
OTM_START="${OTM_START:-1}"

# Key change: Premium range 50-150 (avoid <50 which had 24% win rate)
MAX_PREMIUM="${MAX_PREMIUM:-150}"
MIN_PREMIUM="${MIN_PREMIUM:-50}"

# Key change: Tighter entry criteria
MIN_CONFIDENCE="${MIN_CONFIDENCE:-92}"
MIN_SCORE="${MIN_SCORE:-98}"

MIN_ABS_DELTA="${MIN_ABS_DELTA:-0.15}"

# Key change: Stronger vote difference required (4 instead of 2)
MIN_VOTE_DIFF="${MIN_VOTE_DIFF:-4}"

ADAPTIVE_ENABLE="${ADAPTIVE_ENABLE:-1}"
ADAPTIVE_MODEL_FILE="${ADAPTIVE_MODEL_FILE:-.adaptive_model.json}"
MIN_LEARN_PROB="${MIN_LEARN_PROB:-0.60}"
MIN_MODEL_SAMPLES="${MIN_MODEL_SAMPLES:-20}"
HARD_GATE_MIN_MODEL_SAMPLES="${HARD_GATE_MIN_MODEL_SAMPLES:-100}"
LEARN_GATE_LOCK_STREAK="${LEARN_GATE_LOCK_STREAK:-8}"
LEARN_GATE_RELAX_SEC="${LEARN_GATE_RELAX_SEC:-300}"
TRAIN_MIN_LABELS="${TRAIN_MIN_LABELS:-${MIN_MODEL_SAMPLES}}"
TRAIN_LR="${TRAIN_LR:-0.15}"
TRAIN_EPOCHS="${TRAIN_EPOCHS:-600}"
AUTO_TRAIN_ON_BACKFILL="${AUTO_TRAIN_ON_BACKFILL:-1}"

# Key change: More confirmation (3 instead of 2)
CONFIRM_PULLS="${CONFIRM_PULLS:-3}"

FLIP_COOLDOWN_SEC="${FLIP_COOLDOWN_SEC:-60}"

# Key change: Only 1 strike at a time (reduce fee drag)
MAX_SELECT_STRIKES="${MAX_SELECT_STRIKES:-1}"

MAX_SPREAD_PCT="${MAX_SPREAD_PCT:-2.0}"
SIGNAL_STATE_FILE="${SIGNAL_STATE_FILE:-.signal_state.json}"

PAPER_STATE_FILE="${PAPER_STATE_FILE:-.paper_trade_state.json}"
SHOW_SIGNAL_TABLE="${SHOW_SIGNAL_TABLE:-0}"

echo "=== OPTIMIZED PAPER TRADING ==="
echo "MIN_PREMIUM=$MIN_PREMIUM MAX_PREMIUM=$MAX_PREMIUM"
echo "MIN_CONFIDENCE=$MIN_CONFIDENCE MIN_SCORE=$MIN_SCORE"
echo "MAX_SELECT_STRIKES=$MAX_SELECT_STRIKES CONFIRM_PULLS=$CONFIRM_PULLS"
echo "MIN_VOTE_DIFF=$MIN_VOTE_DIFF MAX_HOLD_SEC=$MAX_HOLD_SEC"
echo "=============================="

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

"${cmd[@]}"
