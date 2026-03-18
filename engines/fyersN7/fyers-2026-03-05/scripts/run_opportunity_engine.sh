#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
ENGINE_SCRIPT="${ROOT_DIR}/scripts/opportunity_engine.py"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Error: ${PYTHON_BIN} not found. Create venv first." >&2
  exit 1
fi

# Initialize daily postmortem folder
source "${ROOT_DIR}/scripts/init_daily_folder.sh"

INTERVAL_SEC="${INTERVAL_SEC:-15}"
# JOURNAL_CSV, EVENTS_CSV, OPP_STATE_FILE set by init_daily_folder.sh
STATE_FILE="${OPP_STATE_FILE}"
START_FROM_LATEST="${START_FROM_LATEST:-1}"

MIN_ENTRY_SCORE="${MIN_ENTRY_SCORE:-74}"
MIN_CONF_ENTRY="${MIN_CONF_ENTRY:-84}"
MIN_VOTE_DIFF_ENTRY="${MIN_VOTE_DIFF_ENTRY:-5}"
MAX_SPREAD_ENTRY="${MAX_SPREAD_ENTRY:-2.2}"
MAX_HOLD_SEC="${MAX_HOLD_SEC:-180}"
FLIP_VOTE_DIFF="${FLIP_VOTE_DIFF:-5}"
EXIT_ON_FLIP="${EXIT_ON_FLIP:-1}"
ENABLE_REVERSAL="${ENABLE_REVERSAL:-1}"
EXIT_ON_REVERSAL="${EXIT_ON_REVERSAL:-1}"
REVERSAL_LOOKBACK="${REVERSAL_LOOKBACK:-20}"
REVERSAL_MIN_POINTS="${REVERSAL_MIN_POINTS:-6}"
REVERSAL_DROP_PCT="${REVERSAL_DROP_PCT:-55}"
REVERSAL_DELTA_DROP="${REVERSAL_DELTA_DROP:-0.12}"
REVERSAL_IV_DROP="${REVERSAL_IV_DROP:-8}"
REVERSAL_MIN_OICH="${REVERSAL_MIN_OICH:-0}"
REVERSAL_MIN_VOL_OI_RATIO="${REVERSAL_MIN_VOL_OI_RATIO:-0}"
REVERSAL_REQUIRE_FLOW="${REVERSAL_REQUIRE_FLOW:-0}"
REVERSAL_REQUIRE_CONTEXT="${REVERSAL_REQUIRE_CONTEXT:-0}"
REVERSAL_BASIS_PCT_CE_MAX="${REVERSAL_BASIS_PCT_CE_MAX:-0.35}"
REVERSAL_MAXPAIN_BAND="${REVERSAL_MAXPAIN_BAND:-90}"
REVERSAL_STRIKE_PCR_CE_MAX="${REVERSAL_STRIKE_PCR_CE_MAX:-1.10}"
REVERSAL_NET_PCR_CE_MAX="${REVERSAL_NET_PCR_CE_MAX:-1.25}"
REVERSAL_PEAK_AGE_SEC="${REVERSAL_PEAK_AGE_SEC:-1800}"
REVERSAL_COOLDOWN_SEC="${REVERSAL_COOLDOWN_SEC:-120}"
REVERSAL_CONF_FLOOR="${REVERSAL_CONF_FLOOR:-88}"

PROFILE="${PROFILE:-expiry}"
LADDER_COUNT="${LADDER_COUNT:-6}"
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
CONFIRM_PULLS="${CONFIRM_PULLS:-2}"
FLIP_COOLDOWN_SEC="${FLIP_COOLDOWN_SEC:-45}"
MAX_SELECT_STRIKES="${MAX_SELECT_STRIKES:-4}"
MAX_SPREAD_PCT="${MAX_SPREAD_PCT:-2.5}"
# SIGNAL_STATE_FILE set by init_daily_folder.sh

SHOW_SIGNAL_TABLE="${SHOW_SIGNAL_TABLE:-0}"
NO_PULL="${NO_PULL:-0}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

acquire_instance_lock "opportunity_engine" || exit 0

cmd=("${PYTHON_BIN}" "${ENGINE_SCRIPT}"
  --interval-sec "${INTERVAL_SEC}"
  --journal-csv "${JOURNAL_CSV}"
  --events-csv "${EVENTS_CSV}"
  --state-file "${STATE_FILE}"
  --min-entry-score "${MIN_ENTRY_SCORE}"
  --min-conf-entry "${MIN_CONF_ENTRY}"
  --min-vote-diff-entry "${MIN_VOTE_DIFF_ENTRY}"
  --max-spread-entry "${MAX_SPREAD_ENTRY}"
  --max-hold-sec "${MAX_HOLD_SEC}"
  --flip-vote-diff "${FLIP_VOTE_DIFF}"
  --reversal-lookback "${REVERSAL_LOOKBACK}"
  --reversal-min-points "${REVERSAL_MIN_POINTS}"
  --reversal-drop-pct "${REVERSAL_DROP_PCT}"
  --reversal-delta-drop "${REVERSAL_DELTA_DROP}"
  --reversal-iv-drop "${REVERSAL_IV_DROP}"
  --reversal-min-oich "${REVERSAL_MIN_OICH}"
  --reversal-min-vol-oi-ratio "${REVERSAL_MIN_VOL_OI_RATIO}"
  --reversal-basis-pct-ce-max "${REVERSAL_BASIS_PCT_CE_MAX}"
  --reversal-maxpain-band "${REVERSAL_MAXPAIN_BAND}"
  --reversal-strike-pcr-ce-max "${REVERSAL_STRIKE_PCR_CE_MAX}"
  --reversal-net-pcr-ce-max "${REVERSAL_NET_PCR_CE_MAX}"
  --reversal-peak-age-sec "${REVERSAL_PEAK_AGE_SEC}"
  --reversal-cooldown-sec "${REVERSAL_COOLDOWN_SEC}"
  --reversal-conf-floor "${REVERSAL_CONF_FLOOR}"
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
  --confirm-pulls "${CONFIRM_PULLS}"
  --flip-cooldown-sec "${FLIP_COOLDOWN_SEC}"
  --max-select-strikes "${MAX_SELECT_STRIKES}"
  --max-spread-pct "${MAX_SPREAD_PCT}"
  --signal-state-file "${SIGNAL_STATE_FILE}")

if [[ "${START_FROM_LATEST}" == "1" ]]; then
  cmd+=(--start-from-latest)
else
  cmd+=(--start-from-beginning)
fi

if [[ "${ADAPTIVE_ENABLE}" == "1" ]]; then
  cmd+=(--enable-adaptive)
else
  cmd+=(--disable-adaptive)
fi

if [[ "${EXIT_ON_FLIP}" == "1" ]]; then
  cmd+=(--exit-on-flip)
else
  cmd+=(--no-exit-on-flip)
fi

if [[ "${ENABLE_REVERSAL}" == "1" ]]; then
  cmd+=(--enable-reversal)
else
  cmd+=(--disable-reversal)
fi

if [[ "${EXIT_ON_REVERSAL}" == "1" ]]; then
  cmd+=(--exit-on-reversal)
else
  cmd+=(--no-exit-on-reversal)
fi

if [[ "${REVERSAL_REQUIRE_FLOW}" == "1" ]]; then
  cmd+=(--reversal-require-flow)
fi

if [[ "${REVERSAL_REQUIRE_CONTEXT}" == "1" ]]; then
  cmd+=(--reversal-require-context)
fi

if [[ "${SHOW_SIGNAL_TABLE}" == "1" ]]; then
  cmd+=(--show-signal-table)
fi

if [[ "${NO_PULL}" == "1" ]]; then
  cmd+=(--no-pull)
fi

if [[ "${SKIP_MARKET_CHECK:-0}" == "1" ]]; then
  cmd+=(--skip-market-check)
fi

if [[ -n "${EXTRA_ARGS}" ]]; then
  # shellcheck disable=SC2206
  extra=(${EXTRA_ARGS})
  cmd+=("${extra[@]}")
fi

"${cmd[@]}"
