#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

MARKET_ADAPTER_HOST="${MARKET_ADAPTER_HOST:-127.0.0.1}"
MARKET_ADAPTER_PORT="${MARKET_ADAPTER_PORT:-8765}"
MARKET_ADAPTER_URL="${MARKET_ADAPTER_URL:-http://${MARKET_ADAPTER_HOST}:${MARKET_ADAPTER_PORT}}"
MARKET_ADAPTER_STRICT="${MARKET_ADAPTER_STRICT:-1}"
export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL MARKET_ADAPTER_STRICT

if [[ -z "${FYERS_WATCHLIST:-}" ]]; then
  echo "❌ FYERS_WATCHLIST is empty."
  echo "   Add this in .env, e.g.:"
  echo "   FYERS_WATCHLIST=NSE:RELIANCE-EQ,NSE:TCS-EQ,NSE:HDFCBANK-EQ"
  exit 1
fi

echo "📈 Running FYERS screener (dry-run strategy)"
echo "   Watchlist: ${FYERS_WATCHLIST}"

# Activate virtual environment
source "$ROOT_DIR/livebench/venv/bin/activate" 2>/dev/null || true

python3 - <<'PY'
import json
import os
from datetime import datetime
from pathlib import Path

from livebench.trading.fyers_client import MarketDataClient
from livebench.trading.screener import run_screener

client = MarketDataClient(fallback_to_local=bool(os.getenv("FYERS_ACCESS_TOKEN")))
result = run_screener(client=client, watchlist=os.getenv("FYERS_WATCHLIST"))

if not result.get("success"):
    print("❌ Screener failed")
    print(f"   Error: {result.get('error')}")
    attempts = result.get("quotes_response", {}).get("attempts")
    if attempts:
        print("   Quote endpoint attempts:")
        for item in attempts:
            print(
                f"    - {item.get('attempt')} | "
                f"status={item.get('status_code')} | "
                f"error={item.get('error')}"
            )
    raise SystemExit(1)

summary = result.get("summary", {})
print("✅ Screener completed")
print(f"   Total: {summary.get('total', 0)}")
print(f"   Buy candidates: {summary.get('buy_candidates', 0)}")
print(f"   Sell candidates: {summary.get('sell_candidates', 0)}")
print(f"   Watch: {summary.get('watch', 0)}")
print(f"   Overbought: {summary.get('overbought', 0)}")
print(f"   Oversold: {summary.get('oversold', 0)}")

warnings = result.get("warnings") or []
missing_symbols = result.get("missing_quote_symbols") or []
if warnings:
    print("\nWarnings:")
    for warning in warnings:
        print(f" - {warning}")
elif missing_symbols:
    print("\nWarnings:")
    print(f" - No quote rows returned for: {', '.join(missing_symbols)}")

print("\nTop signals:")
for row in result.get("results", [])[:10]:
    symbol = row.get("symbol")
    signal = row.get("signal")
    chg = row.get("change_pct")
    ltp = row.get("last_price")
    reason = row.get("reason")
    chg_text = "NA" if chg is None else f"{chg:.2f}%"
    ltp_text = "NA" if ltp is None else f"{ltp:.2f}"
    print(f" - {symbol}: {signal} | LTP={ltp_text} | Change={chg_text} | {reason}")

print("\nIndex strike recommendations:")
for row in result.get("index_recommendations", []):
    side = row.get("option_side")
    strike = row.get("preferred_strike")
    confidence = row.get("confidence")
    chg = row.get("change_pct")
    chg_text = "NA" if chg is None else f"{chg:.2f}%"
    strike_text = "WAIT" if strike is None else str(strike)
    print(
        f" - {row.get('index')}: {row.get('signal')} | Side={side} | "
        f"Change={chg_text} | Preferred Strike={strike_text} | Confidence={confidence}%"
    )

out_dir = Path("livebench/data/fyers")
out_dir.mkdir(parents=True, exist_ok=True)
out_file = out_dir / f"screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved full result: {out_file}")
PY
