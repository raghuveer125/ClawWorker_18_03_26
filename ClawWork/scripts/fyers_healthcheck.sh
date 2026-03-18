#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

echo "🔎 Running FYERS health check"
echo "   Working directory: $ROOT_DIR"

python - <<'PY'
import json
import sys

from livebench.trading.fyers_client import FyersClient

client = FyersClient()

profile = client.profile()
if not profile.get("success"):
    print("❌ FYERS profile check failed")
    print(f"   Status: {profile.get('status_code')}")
    print(f"   Error: {profile.get('error')}")
    sys.exit(1)

funds = client.funds()
if not funds.get("success"):
    print("⚠️ FYERS profile is OK, but funds check failed")
    print(f"   Status: {funds.get('status_code')}")
    print(f"   Error: {funds.get('error')}")
    sys.exit(1)

profile_data = profile.get("data", {})
funds_data = funds.get("data", {})

print("✅ FYERS health check passed")
print(f"   Profile keys: {', '.join(sorted(profile_data.keys())[:8])}")
print(f"   Funds keys: {', '.join(sorted(funds_data.keys())[:8])}")
print("   Token is valid and account endpoints are reachable.")
PY
