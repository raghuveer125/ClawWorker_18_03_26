#!/bin/bash

# Quick Test Script for LiveBench Dashboard
# Runs an agent with specified config to populate the dashboard
#
# Usage:
#   ./run_test_agent.sh                              # Uses default inline config
#   ./run_test_agent.sh livebench/configs/test_glm47.json

# Get config file from argument or use default
CONFIG_FILE=${1:-"livebench/configs/example_inline_tasks.json"}

echo "🎯 LiveBench Agent Test"
echo "===================================="
echo ""
echo "📋 Config: $CONFIG_FILE"
echo ""

# Activate Python environment (conda or venv)
echo "🔧 Activating Python environment..."

# Try conda first, fall back to venv
if command -v conda &> /dev/null; then
    source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null || true
    if conda info --envs 2>/dev/null | awk '{print $1}' | grep -qx "livebench"; then
        conda activate livebench
        echo "   Using conda env: livebench"
    else
        echo "⚠️  Conda env 'livebench' not found; trying venv..."
        if [ -d "livebench/venv" ]; then
            source livebench/venv/bin/activate
            echo "   Using venv: livebench/venv"
        else
            echo "⚠️  No venv found; using system Python"
        fi
    fi
elif [ -d "livebench/venv" ]; then
    source livebench/venv/bin/activate
    echo "   Using venv: livebench/venv"
else
    echo "⚠️  No conda or venv found; using system Python"
fi

echo "   Using Python: $(which python)"
echo ""

# Load environment variables from .env if it exists
if [ -f ".env" ]; then
    echo "📝 Loading environment variables from .env..."
    set -a
    source .env
    set +a
    echo ""
fi

# Validate config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Config file not found: $CONFIG_FILE"
    echo ""
    echo "Available configs:"
    ls -1 livebench/configs/*.json 2>/dev/null || echo "  (none found)"
    echo ""
    exit 1
fi
echo "✓ Config file found"
echo ""

# Auto-fallback: if config expects GDPVal parquet but dataset is absent, use inline config
if grep -q '"gdpval_path"' "$CONFIG_FILE"; then
    if [ ! -f "./gdpval/data/train-00000-of-00001.parquet" ]; then
        FALLBACK_CONFIG="livebench/configs/example_inline_tasks.json"
        echo "⚠️  GDPVal parquet not found at ./gdpval/data/train-00000-of-00001.parquet"
        echo "   Switching to inline test config: $FALLBACK_CONFIG"
        CONFIG_FILE="$FALLBACK_CONFIG"
        echo ""
    fi
fi

# Check environment variables
echo "🔍 Checking environment..."

is_placeholder_key() {
    local value="$1"
    local lower
    lower=$(echo "$value" | tr '[:upper:]' '[:lower:]')

    if [[ -z "$value" ]]; then
        return 0
    fi

    case "$lower" in
        your-*|*your-api-key*|*placeholder*|*changeme*|*example*|*dummy*|*test-key*)
            return 0
            ;;
    esac

    return 1
}

if is_placeholder_key "$OPENAI_API_KEY"; then
    echo "❌ OPENAI_API_KEY not set"
    echo "   Please set a real key in .env or export OPENAI_API_KEY='<real-key>'"
    exit 1
fi
echo "✓ OPENAI_API_KEY set"

if is_placeholder_key "$WEB_SEARCH_API_KEY"; then
    echo "❌ WEB_SEARCH_API_KEY not set"
    echo "   Please set a real key in .env or export WEB_SEARCH_API_KEY='<real-key>'"
    echo "   You can also set WEB_SEARCH_PROVIDER (default: tavily)"
    exit 1
fi
echo "✓ WEB_SEARCH_API_KEY set"

if [ -n "$EVALUATION_API_KEY" ] && is_placeholder_key "$EVALUATION_API_KEY"; then
    echo "❌ EVALUATION_API_KEY is a placeholder value"
    echo "   Set a real EVALUATION_API_KEY, or unset it to fall back to OPENAI_API_KEY"
    exit 1
fi

if is_placeholder_key "$E2B_API_KEY"; then
    export LIVEBENCH_DISABLE_WRAPUP=1
    echo "⚠️  E2B_API_KEY missing/placeholder -> disabling wrap-up workflow for this run"
else
    export LIVEBENCH_DISABLE_WRAPUP=${LIVEBENCH_DISABLE_WRAPUP:-0}
fi

echo ""

# Set MCP port if not set
export LIVEBENCH_HTTP_PORT=${LIVEBENCH_HTTP_PORT:-8010}

# Add project root to PYTHONPATH to ensure imports work
export PYTHONPATH="$(pwd):$PYTHONPATH"

# Extract agent info from config (macOS-compatible parsing)
AGENT_NAME=$(sed -n 's/.*"signature"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG_FILE" | head -1)
BASEMODEL=$(sed -n 's/.*"basemodel"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG_FILE" | head -1)
INIT_DATE=$(sed -n 's/.*"init_date"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG_FILE" | head -1)
END_DATE=$(sed -n 's/.*"end_date"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG_FILE" | head -1)
INITIAL_BALANCE=$(sed -n 's/.*"initial_balance"[[:space:]]*:[[:space:]]*\([0-9.]*\).*/\1/p' "$CONFIG_FILE" | head -1)
export LIVEBENCH_BASEMODEL="$BASEMODEL"

echo "===================================="
echo "🤖 Running Agent"
echo "===================================="
echo ""
echo "Configuration:"
echo "  - Config: $(basename $CONFIG_FILE)"
echo "  - Agent: ${AGENT_NAME:-unknown}"
echo "  - Model: ${BASEMODEL:-unknown}"
echo "  - Date Range: ${INIT_DATE:-N/A} to ${END_DATE:-N/A}"
echo "  - Initial Balance: \$${INITIAL_BALANCE:-1000}"

if [ "${LIVEBENCH_DISABLE_WRAPUP:-0}" = "1" ]; then
    echo "  - E2B Wrap-up: disabled (missing/placeholder E2B_API_KEY)"
else
    if [ -n "${E2B_TEMPLATE_ID:-}" ]; then
        echo "  - E2B Template: E2B_TEMPLATE_ID=${E2B_TEMPLATE_ID}"
    elif [ -n "${E2B_TEMPLATE_ALIAS:-}" ]; then
        echo "  - E2B Template: E2B_TEMPLATE_ALIAS=${E2B_TEMPLATE_ALIAS}"
    elif [ -n "${E2B_TEMPLATE:-}" ]; then
        echo "  - E2B Template: E2B_TEMPLATE=${E2B_TEMPLATE}"
    else
        echo "  - E2B Template: fallback order -> gdpval-workspace, then E2B default template"
    fi
fi
echo ""

if [ "${LIVEBENCH_SKIP_API_PREFLIGHT:-0}" != "1" ]; then
    echo "🧪 Running API preflight check..."
    python - <<'PY'
import os
import sys
from openai import OpenAI

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("❌ OPENAI_API_KEY is not set")
    sys.exit(1)

base_url = os.getenv("OPENAI_API_BASE")
model = os.getenv("LIVEBENCH_PREFLIGHT_MODEL") or os.getenv("LIVEBENCH_BASEMODEL") or "gpt-4o-mini"

try:
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=1,
    )
    print(f"✓ API preflight passed ({model})")
except Exception as e:
    msg = str(e)
    msg_lower = msg.lower()
    if "insufficient_quota" in msg_lower or "exceeded your current quota" in msg_lower:
        print("❌ API quota exhausted (429): check billing/quota or use a different provider/key")
    elif "incorrect api key" in msg_lower or "invalid_api_key" in msg_lower:
        print("❌ Invalid OPENAI_API_KEY: update your key and retry")
    else:
        print(f"❌ API preflight failed: {msg[:200]}")
    sys.exit(1)
PY
    if [ $? -ne 0 ]; then
        exit 1
    fi
    echo ""
fi

echo "Note: The agent will handle MCP service internally"
echo ""
echo "This will take a few minutes..."
echo ""
echo "===================================="
echo ""

# Run the agent with specified config
python livebench/main.py "$CONFIG_FILE"

echo ""
echo "===================================="
echo "✅ Test completed!"
echo "===================================="
echo ""
echo "📊 View results in dashboard:"
echo "   http://localhost:3001"
echo ""
echo "🔧 API endpoints:"
echo "   http://localhost:8001/api/agents"
echo "   http://localhost:8001/docs"
echo ""
