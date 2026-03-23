#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  source "${ROOT_DIR}/.env"
  set +a
elif [[ -f "${WORKSPACE_ROOT}/.env" ]]; then
  set -a
  source "${WORKSPACE_ROOT}/.env"
  set +a
fi

MARKET_ADAPTER_HOST="${MARKET_ADAPTER_HOST:-127.0.0.1}"
MARKET_ADAPTER_PORT="${MARKET_ADAPTER_PORT:-8765}"
MARKET_ADAPTER_URL="${MARKET_ADAPTER_URL:-http://${MARKET_ADAPTER_HOST}:${MARKET_ADAPTER_PORT}}"
MARKET_ADAPTER_STRICT="${MARKET_ADAPTER_STRICT:-0}"
export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL MARKET_ADAPTER_STRICT

echo "📈 Running FYERS screener (multi-index dry-run strategy)"

PYTHON_BIN="${WORKSPACE_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="${ROOT_DIR}/livebench/venv/bin/python"
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

"${PYTHON_BIN}" - <<'PY'
import json
import os
from datetime import datetime
from pathlib import Path

from livebench.trading.fyers_client import build_market_data_client
from livebench.trading.screener import run_screener

client = build_market_data_client()
result = run_screener(client=client)

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
baskets = result.get("watchlist_baskets") or {}
print("✅ Screener completed")
print(f"   Unique symbols screened: {len(result.get('watchlist', []))}")
print(f"   Total: {summary.get('total', 0)}")
print(f"   Buy candidates: {summary.get('buy_candidates', 0)}")
print(f"   Sell candidates: {summary.get('sell_candidates', 0)}")
print(f"   Watch: {summary.get('watch', 0)}")
print(f"   Overbought: {summary.get('overbought', 0)}")
print(f"   Oversold: {summary.get('oversold', 0)}")
if baskets:
    print("   Baskets:")
    for basket_name, basket_symbols in baskets.items():
        print(f"    - {basket_name}: {len(basket_symbols)} symbols")

basket_summaries = result.get("basket_summaries") or []
if basket_summaries:
    print("\nBasket summary:")
    for row in basket_summaries:
        print(
            f" - {row.get('basket')}: Total={row.get('total', 0)} | "
            f"Buy={row.get('buy_candidates', 0)} | "
            f"Sell={row.get('sell_candidates', 0)} | "
            f"Watch={row.get('watch', 0)} | "
            f"OB={row.get('overbought', 0)} | "
            f"OS={row.get('oversold', 0)} | "
            f"Missing={row.get('missing_quotes', 0)}"
        )

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
