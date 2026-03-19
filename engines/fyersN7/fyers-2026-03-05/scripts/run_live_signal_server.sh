#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
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

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

DATE="${1:-$(TZ=Asia/Kolkata date +%F)}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8787}"
INTERVAL="${INTERVAL:-15}"
POLL_INTERVAL="${POLL_INTERVAL:-2}"
# Get indices from shared config if not set
if [[ -z "${INDICES:-}" ]]; then
  INDICES=$("$PYTHON_BIN" -c "
import sys; sys.path.insert(0, '$PROJECT_ROOT')
try:
    from shared_project_engine.indices import ACTIVE_INDICES
    print(','.join(ACTIVE_INDICES))
except ImportError:
    print('SENSEX,NIFTY50,BANKNIFTY,FINNIFTY')
" 2>/dev/null || echo "SENSEX,NIFTY50,BANKNIFTY,FINNIFTY")
fi
EVENTS_FILE="${EVENTS_FILE:-opportunity_events.csv}"
EVENTS_LIMIT="${EVENTS_LIMIT:-20}"
BASE_DIR="${BASE_DIR:-$ROOT_DIR/postmortem}"
OUTPUT="${OUTPUT:-$BASE_DIR/$DATE/live_signal_view.html}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python not found/executable at: $PYTHON_BIN"
  echo "Set PYTHON_BIN or create .venv first."
  exit 1
fi

echo "Starting live signal web server..."
echo "- URL: http://$HOST:$PORT"
echo "- Date: $DATE"
echo "- Indices: $INDICES"
echo "- Events source: $EVENTS_FILE (limit=${EVENTS_LIMIT})"
echo "- Rebuild interval: ${INTERVAL}s"
echo "- Browser poll interval: ${POLL_INTERVAL}s"
echo "- Base dir: $BASE_DIR"
echo "- Output cache file: $OUTPUT"

exec "$PYTHON_BIN" "$SCRIPT_DIR/run_live_signal_server.py" \
  --host "$HOST" \
  --port "$PORT" \
  --base-dir "$BASE_DIR" \
  --date "$DATE" \
  --indices "$INDICES" \
  --source-file "decision_journal.csv" \
  --events-file "$EVENTS_FILE" \
  --events-limit "$EVENTS_LIMIT" \
  --interval "$INTERVAL" \
  --poll-interval "$POLL_INTERVAL" \
  --output "$OUTPUT"
