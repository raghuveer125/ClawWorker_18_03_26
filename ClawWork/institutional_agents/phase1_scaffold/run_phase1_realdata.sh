#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE="../../.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${FYERS_ACCESS_TOKEN:-}" ]]; then
  echo "ERROR: FYERS_ACCESS_TOKEN is not set."
  echo "Set it in shell or in ClawWork/.env, then rerun."
  echo "Example: export FYERS_ACCESS_TOKEN='<token>'"
  exit 2
fi

python phase1_realdata_runner.py \
  --from-date 2026-01-01 \
  --to-date 2026-02-18 \
  --resolution D \
  --underlyings NIFTY50,BANKNIFTY,SENSEX \
  --min-rows 20 \
  --min-trading-days 20 \
  --outdir reports \
  --tag phase1_20day

echo
echo "Summary artifact: reports/phase1_20day_realdata_run_summary.json"
