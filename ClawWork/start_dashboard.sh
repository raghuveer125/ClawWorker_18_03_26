#!/bin/bash

# LiveBench Dashboard Startup Script
# This script starts all services: Auth, Backend, Frontend, Tunnel, Screener

set -e

SCREENER_ENABLED="${SCREENER_ENABLED:-1}"
SCREENER_INTERVAL_SECONDS="${SCREENER_INTERVAL_SECONDS:-30}"
SKIP_AUTH="${SKIP_AUTH:-0}"
if [ -z "${DETACH_AFTER_START:-}" ]; then
    if [ -t 1 ]; then
        DETACH_AFTER_START=0
    else
        DETACH_AFTER_START=1
    fi
fi
SCREENER_PID=""
TUNNEL_PID=""

# Project directories
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$PROJECT_DIR")"
SHARED_ENGINE_DIR="$(dirname "$PROJECT_DIR")/shared_project_engine"

# Load port configuration from shared_project_engine if available
_load_ports() {
  local config
  config=$(python3 -c "
import sys
sys.path.insert(0, '$(dirname "$PROJECT_DIR")')
try:
    from shared_project_engine.services import API_PORT, FRONTEND_PORT
    print(f'_SHARED_API_PORT={API_PORT}')
    print(f'_SHARED_FRONTEND_PORT={FRONTEND_PORT}')
except ImportError:
    print('_SHARED_API_PORT=8001')
    print('_SHARED_FRONTEND_PORT=3001')
" 2>/dev/null) || true
  eval "${config}"
}
_load_ports

# Port configuration (env > shared config > defaults)
API_PORT="${API_PORT:-${_SHARED_API_PORT:-8001}}"
FRONTEND_PORT="${FRONTEND_PORT:-${_SHARED_FRONTEND_PORT:-3001}}"
LIVEBENCH_DIR="$PROJECT_DIR/livebench"
FRONTEND_DIR="$PROJECT_DIR/frontend"

# Load .env file if it exists
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a  # Automatically export all variables
    source "$PROJECT_DIR/.env"
    set +a
fi

# Trading mode from .env (defaults to paper mode for safety)
FYERS_DRY_RUN="${FYERS_DRY_RUN:-true}"
FYERS_ALLOW_LIVE_ORDERS="${FYERS_ALLOW_LIVE_ORDERS:-false}"

# Cloudflare Tunnel Token
TUNNEL_TOKEN="eyJhIjoiYjdlNjA1MmEwNTk4Y2JmNjE5ZWNkZDE0NjgyZDQ4MjYiLCJ0IjoiYWJiMGQ4NGItYTA4YS00OTIwLWJmYmYtYjFhNTMwNDQ0NGQ0IiwicyI6Ik1tUTRPREl4TkdNdFpEbGpNeTAwWkRkbExXRTJORFV0T1dRMU16azNaRE5sT0dJeiJ9"

echo "=============================================="
echo "  LiveBench Dashboard Startup"
echo "=============================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 is not installed${NC}"
    exit 1
fi

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo -e "${RED}Node.js is not installed${NC}"
    exit 1
fi

# Check if cloudflared is installed
if ! command -v cloudflared &> /dev/null; then
    echo -e "${YELLOW}cloudflared not installed - tunnel will be skipped${NC}"
    TUNNEL_ENABLED=0
else
    TUNNEL_ENABLED=1
fi

# Function to kill existing processes on a port
kill_port() {
    local port=$1
    local name=$2
    local pid=$(lsof -ti:$port 2>/dev/null)

    if [ -n "$pid" ]; then
        echo -e "${YELLOW}Found existing $name (PID: $pid) on port $port - killing...${NC}"
        kill -9 $pid 2>/dev/null
        sleep 1
    fi
}

should_detach() {
    [ "${DETACH_AFTER_START}" = "1" ]
}

ensure_frontend_dependencies() {
    if [ -x "$FRONTEND_DIR/node_modules/.bin/vite" ]; then
        return 0
    fi

    echo -e "${YELLOW}Frontend dev dependencies missing or incomplete - running npm install --include=dev...${NC}"
    cd "$FRONTEND_DIR"
    npm install --include=dev
    cd "$PROJECT_DIR"
}

find_activate_script() {
    local candidate
    for candidate in \
        "$PROJECT_DIR/.venv/bin/activate" \
        "$REPO_ROOT/.venv/bin/activate" \
        "$LIVEBENCH_DIR/venv/bin/activate"
    do
        if [ -f "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

# Function to cleanup on exit
cleanup() {
    echo ""
    echo -e "${BLUE}Stopping all services...${NC}"
    [ -n "${TUNNEL_PID:-}" ] && kill "$TUNNEL_PID" 2>/dev/null || true
    [ -n "${SCREENER_PID:-}" ] && kill "$SCREENER_PID" 2>/dev/null || true
    [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null || true
    [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null || true
    exit 0
}

trap cleanup INT TERM

# Create logs directory
mkdir -p "$PROJECT_DIR/logs"

# ============================================
# Step 1: FYERS Auto Authentication
# ============================================
if [ "$SKIP_AUTH" != "1" ]; then
    echo -e "${BLUE}[1/5] FYERS Auto Authentication${NC}"

    # Activate virtual environment for auth
    ACTIVATE_SCRIPT="$(find_activate_script || true)"
    if [ -n "${ACTIVATE_SCRIPT:-}" ]; then
        # shellcheck disable=SC1090
        source "$ACTIVATE_SCRIPT"
    fi

    if [ -f "$PROJECT_DIR/scripts/fyers_auto_auth.py" ]; then
        python3 "$PROJECT_DIR/scripts/fyers_auto_auth.py"
        if [ $? -ne 0 ]; then
            echo -e "${YELLOW}Auth failed or skipped - continuing anyway${NC}"
        fi
    else
        echo -e "${YELLOW}Auth script not found - skipping${NC}"
    fi
    echo ""
else
    echo -e "${YELLOW}[1/5] Skipping auth (SKIP_AUTH=1)${NC}"
    echo ""
fi

# ============================================
# Step 2: Kill existing processes
# ============================================
echo -e "${BLUE}[2/5] Checking for existing services...${NC}"
kill_port $API_PORT "Backend API"
kill_port $FRONTEND_PORT "Frontend"
echo ""

# ============================================
# Step 3: Start Backend API
# ============================================
echo -e "${BLUE}[3/5] Starting Backend API...${NC}"

# Setup virtual environment for livebench if needed
if [ ! -d "$LIVEBENCH_DIR/venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv "$LIVEBENCH_DIR/venv"
    source "$LIVEBENCH_DIR/venv/bin/activate"
    echo -e "${YELLOW}Installing dependencies from requirements.txt...${NC}"
    pip install --upgrade pip
    pip install fastapi uvicorn websockets httpx aiofiles
    if [ -f "$LIVEBENCH_DIR/requirements.txt" ]; then
        pip install -r "$LIVEBENCH_DIR/requirements.txt"
    fi
else
    source "$LIVEBENCH_DIR/venv/bin/activate"
    # Ensure critical dependencies are installed
    pip install --quiet fastapi uvicorn websockets httpx aiofiles 2>/dev/null || true
fi

nohup bash -lc "cd '$LIVEBENCH_DIR' && if [ -x '$LIVEBENCH_DIR/venv/bin/python' ]; then exec '$LIVEBENCH_DIR/venv/bin/python' -m uvicorn api.server:app --host 0.0.0.0 --port '$API_PORT'; else exec python3 -m uvicorn api.server:app --host 0.0.0.0 --port '$API_PORT'; fi" > "$PROJECT_DIR/logs/api.log" 2>&1 &
API_PID=$!

sleep 2

if ! kill -0 $API_PID 2>/dev/null; then
    echo -e "${RED}Failed to start Backend API${NC}"
    echo "Check logs/api.log for details"
    exit 1
fi

echo -e "${GREEN}Backend API started (PID: $API_PID)${NC}"
echo ""

# ============================================
# Step 4: Start Frontend
# ============================================
echo -e "${BLUE}[4/5] Starting Frontend Dashboard...${NC}"

ensure_frontend_dependencies

nohup bash -lc "cd '$FRONTEND_DIR' && exec npm run dev -- --port '$FRONTEND_PORT'" > "$PROJECT_DIR/logs/frontend.log" 2>&1 &
FRONTEND_PID=$!

sleep 3

if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo -e "${RED}Failed to start Frontend${NC}"
    echo "Check logs/frontend.log for details"
    kill $API_PID 2>/dev/null
    exit 1
fi

echo -e "${GREEN}Frontend started (PID: $FRONTEND_PID)${NC}"
echo ""

# ============================================
# Step 5: Start Cloudflare Tunnel
# ============================================
if [ "$TUNNEL_ENABLED" = "1" ]; then
    echo -e "${BLUE}[5/5] Starting Cloudflare Tunnel...${NC}"

    nohup cloudflared tunnel run --protocol http2 --token "$TUNNEL_TOKEN" > "$PROJECT_DIR/logs/tunnel.log" 2>&1 &
    TUNNEL_PID=$!

    sleep 3

    if ! kill -0 $TUNNEL_PID 2>/dev/null; then
        echo -e "${YELLOW}Tunnel failed to start - check logs/tunnel.log${NC}"
    else
        echo -e "${GREEN}Cloudflare Tunnel started (PID: $TUNNEL_PID)${NC}"
    fi
    echo ""
else
    echo -e "${YELLOW}[5/5] Skipping tunnel (cloudflared not installed)${NC}"
    echo ""
fi

# ============================================
# Start FYERS Screener Loop (optional)
# ============================================
if [ "$SCREENER_ENABLED" = "1" ]; then
    if [ -f "$PROJECT_DIR/scripts/fyers_screener.sh" ]; then
        echo -e "${BLUE}Starting FYERS screener loop (${SCREENER_INTERVAL_SECONDS}s)...${NC}"
        nohup bash -lc "while true; do bash '$PROJECT_DIR/scripts/fyers_screener.sh' >> '$PROJECT_DIR/logs/screener.log' 2>&1 || true; sleep '$SCREENER_INTERVAL_SECONDS'; done" > /dev/null 2>&1 &
        SCREENER_PID=$!
        echo -e "${GREEN}Screener loop started (PID: $SCREENER_PID)${NC}"
    fi
fi

echo ""
echo -e "${GREEN}=============================================="
echo -e "  LiveBench Dashboard is running!"
echo -e "==============================================${NC}"
echo ""
echo -e "  ${BLUE}Local:${NC}     http://localhost:$FRONTEND_PORT"
echo -e "  ${BLUE}Remote:${NC}    https://trading.bhoomidaksh.xyz"
echo -e "  ${BLUE}API:${NC}       http://localhost:$API_PORT"
echo -e "  ${BLUE}API Docs:${NC}  http://localhost:$API_PORT/docs"
echo ""

# Show Trading Mode Status
echo -e "${BLUE}Trading Mode:${NC}"
if [ "$FYERS_DRY_RUN" = "true" ] || [ "$FYERS_ALLOW_LIVE_ORDERS" = "false" ]; then
    echo -e "  ${GREEN}PAPER MODE${NC} - No real money at risk"
    echo -e "  DRY_RUN=$FYERS_DRY_RUN, ALLOW_LIVE_ORDERS=$FYERS_ALLOW_LIVE_ORDERS"
else
    echo -e "  ${RED}LIVE MODE${NC} - REAL MONEY TRADING ENABLED!"
    echo -e "  ${RED}DRY_RUN=$FYERS_DRY_RUN, ALLOW_LIVE_ORDERS=$FYERS_ALLOW_LIVE_ORDERS${NC}"
fi
echo ""
echo -e "${BLUE}Auto-Trader API:${NC}"
echo -e "  Default:   AutoTrader is the paper trading engine for this stack"
echo -e "  Status:    curl http://localhost:$API_PORT/api/auto-trader/status"
echo -e "  Start:     curl http://localhost:$API_PORT/api/auto-trader/start"
echo -e "  Stop:      curl http://localhost:$API_PORT/api/auto-trader/stop"
echo -e "  Loop:      curl http://localhost:$API_PORT/api/auto-trader/loop-status"
echo -e "  Mode:      curl http://localhost:$API_PORT/api/auto-trader/trading-mode"
echo ""
echo -e "${BLUE}Logs:${NC}"
echo -e "  API:      tail -f logs/api.log"
echo -e "  Frontend: tail -f logs/frontend.log"
echo -e "  Tunnel:   tail -f logs/tunnel.log"
echo -e "  Screener: tail -f logs/screener.log"
echo ""
echo -e "${BLUE}Options:${NC}"
echo -e "  SKIP_AUTH=1 ./start_dashboard.sh    # Skip FYERS auth"
echo -e "  SCREENER_ENABLED=0 ./start_dashboard.sh  # Disable screener"
echo -e "  DETACH_AFTER_START=1 ./start_dashboard.sh  # Start and exit"
echo ""
echo -e "${BLUE}Trading Mode:${NC}"
echo -e "  Edit .env to change FYERS_DRY_RUN and FYERS_ALLOW_LIVE_ORDERS"
echo -e "  FYERS_DRY_RUN=true (default) for paper trading"
echo -e "  FYERS_DRY_RUN=false + FYERS_ALLOW_LIVE_ORDERS=true for live"
echo ""
if should_detach; then
    echo -e "${YELLOW}Detached startup complete. Services continue in the background.${NC}"
    echo -e "${YELLOW}Use logs/ and port checks to verify health; stop services by PID or port if needed.${NC}"
    exit 0
fi

echo -e "${RED}Press Ctrl+C to stop all services${NC}"
echo ""

# Keep script running
wait
