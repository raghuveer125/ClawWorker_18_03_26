#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Stopping all Fyers engines..."

# Find and kill Python processes related to the engines
PATTERNS=(
  "pull_fyers_signal.py"
  "opportunity_engine.py"
  "update_adaptive_model.py"
  "paper_trade_loop.py"
  "run_live_signal_server.py"
)

killed=0
for pattern in "${PATTERNS[@]}"; do
  pids=$(pgrep -f "${pattern}" 2>/dev/null || true)
  if [[ -n "${pids}" ]]; then
    echo "Killing ${pattern} (PIDs: ${pids})"
    echo "${pids}" | xargs kill 2>/dev/null || true
    ((killed+=1))
  fi
done

# Kill shell wrapper scripts
SHELL_PATTERNS=(
  "run_signal_loop.sh"
  "run_opportunity_engine.sh"
  "run_two_engines.sh"
  "run_paper_trade_loop.sh"
  "run_live_signal_server.sh"
  "start_all.sh run"
  "start_all.sh paper"
)

for pattern in "${SHELL_PATTERNS[@]}"; do
  pids=$(pgrep -f "${pattern}" 2>/dev/null || true)
  if [[ -n "${pids}" ]]; then
    echo "Killing ${pattern} (PIDs: ${pids})"
    echo "${pids}" | xargs kill 2>/dev/null || true
    ((killed+=1))
  fi
done

if [[ ${killed} -eq 0 ]]; then
  echo "No running engines found."
else
  echo "Done. Stopped ${killed} process group(s)."
fi
