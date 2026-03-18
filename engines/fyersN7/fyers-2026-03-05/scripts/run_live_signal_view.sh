#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

DATE="${1:-$(TZ=Asia/Kolkata date +%F)}"
INTERVAL="${INTERVAL:-15}"
# Get indices from shared config if not set
if [[ -z "${INDICES:-}" ]]; then
  INDICES=$("$PYTHON_BIN" -c "
import sys; sys.path.insert(0, '$ROOT_DIR/../..')
try:
    from shared_project_engine.indices import ACTIVE_INDICES
    print(','.join(ACTIVE_INDICES))
except ImportError:
    print('SENSEX,NIFTY50,BANKNIFTY,FINNIFTY')
" 2>/dev/null || echo "SENSEX,NIFTY50,BANKNIFTY,FINNIFTY")
fi
BASE_DIR="${BASE_DIR:-$ROOT_DIR/postmortem}"
OUTPUT="${OUTPUT:-$BASE_DIR/$DATE/live_signal_view.html}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python not found/executable at: $PYTHON_BIN"
  echo "Set PYTHON_BIN or create .venv first."
  exit 1
fi

echo "Starting live signal view..."
echo "- Date: $DATE"
echo "- Indices: $INDICES"
echo "- Interval: ${INTERVAL}s"
echo "- Base dir: $BASE_DIR"
echo "- Output: $OUTPUT"

exec "$PYTHON_BIN" "$SCRIPT_DIR/generate_live_signal_view.py" \
  --base-dir "$BASE_DIR" \
  --date "$DATE" \
  --indices "$INDICES" \
  --interval "$INTERVAL" \
  --watch \
  --output "$OUTPUT"
