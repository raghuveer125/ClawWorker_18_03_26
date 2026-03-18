#!/bin/bash
# Paper Trading Integration Script
# Starts the complete paper trading setup with Phase 1B configuration

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$PROJECT_ROOT")"
API_LOG="$PROJECT_ROOT/logs/api_server.log"
FRONTEND_LOG="$PROJECT_ROOT/logs/frontend.log"
MARKET_ADAPTER_LOG="$PROJECT_ROOT/logs/market_adapter.log"
MARKET_ADAPTER_PID_FILE="$PROJECT_ROOT/logs/market_adapter.pid"
SCREENER_LOG="$PROJECT_ROOT/logs/fyers_screener_loop.log"
MARKET_ADAPTER_STARTED=0
MARKET_ADAPTER_PID=""
MARKET_ADAPTER_HELPER="${WORKSPACE_ROOT}/shared_project_engine/launcher/market_adapter.sh"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Load port configuration from shared_project_engine if available
_load_ports() {
  local config
  config=$(python3 -c "
import sys
sys.path.insert(0, '${WORKSPACE_ROOT}')
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

if [[ -f "${MARKET_ADAPTER_HELPER}" ]]; then
  # shellcheck source=/dev/null
  source "${MARKET_ADAPTER_HELPER}"
  load_market_adapter_config "${WORKSPACE_ROOT}" "python3"
else
  MARKET_ADAPTER_HOST="${MARKET_ADAPTER_HOST:-127.0.0.1}"
  MARKET_ADAPTER_PORT="${MARKET_ADAPTER_PORT:-8765}"
  MARKET_ADAPTER_URL="${MARKET_ADAPTER_URL:-http://${MARKET_ADAPTER_HOST}:${MARKET_ADAPTER_PORT}}"
  export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL
fi
MARKET_ADAPTER_STRICT="${MARKET_ADAPTER_STRICT:-1}"
export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL MARKET_ADAPTER_STRICT

# Configuration (env > shared config > defaults)
API_PORT=${API_PORT:-${_SHARED_API_PORT:-8001}}
FRONTEND_PORT=${FRONTEND_PORT:-${_SHARED_FRONTEND_PORT:-3001}}
# Create logs directory
mkdir -p "$PROJECT_ROOT/logs"
mkdir -p "$PROJECT_ROOT/livebench/data"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Paper Trading Integration - AutoTrader Default          ║${NC}"
echo -e "${BLUE}║   Live Dashboard: http://localhost:$FRONTEND_PORT                       ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"

# Function to cleanup on exit
cleanup() {
  echo -e "${YELLOW}⏹️  Shutting down paper trading system...${NC}"
  
  # Kill all background processes
  jobs -p | xargs -r kill 2>/dev/null || true
  
  # Wait a bit for graceful shutdown
  sleep 2
  
  # Force kill any remaining processes
  lsof -ti :$API_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
  lsof -ti :$FRONTEND_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
  if [[ "${MARKET_ADAPTER_STARTED}" == "1" ]] && [[ -n "${MARKET_ADAPTER_PID}" ]]; then
    kill "${MARKET_ADAPTER_PID}" 2>/dev/null || true
  fi
  rm -f "$MARKET_ADAPTER_PID_FILE"
  
  echo -e "${RED}✓ Shutdown complete${NC}"
  exit 0
}

trap cleanup SIGINT SIGTERM

# Function to check if port is available
check_port() {
  if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${RED}✗ Port $1 is already in use${NC}"
    return 1
  fi
  return 0
}

# Function to wait for service to be ready
wait_for_service() {
  local url=$1
  local service=$2
  local max_wait=30
  local elapsed=0
  
  echo -e "${YELLOW}⏳ Waiting for $service to be ready...${NC}"
  
  while ! curl -s "$url" > /dev/null 2>&1; do
    if [ $elapsed -ge $max_wait ]; then
      echo -e "${RED}✗ $service failed to start${NC}"
      return 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  
  echo -e "${GREEN}✓ $service is ready (${elapsed}s)${NC}"
  return 0
}

# Check ports
echo -e "${BLUE}Checking ports...${NC}"
check_port $API_PORT || exit 1
check_port $FRONTEND_PORT || exit 1

echo -e "${BLUE}Starting shared market adapter on $MARKET_ADAPTER_URL...${NC}"
if ! ensure_market_adapter_running "${WORKSPACE_ROOT}" "python3" "${MARKET_ADAPTER_LOG}" "${MARKET_ADAPTER_PID_FILE}" "${WORKSPACE_ROOT}/.env" "${WORKSPACE_ROOT}"; then
  exit 1
fi
if [[ "${MARKET_ADAPTER_STARTED}" == "1" ]]; then
  echo -e "${GREEN}✓ Market Adapter PID: $MARKET_ADAPTER_PID${NC}"
else
  echo -e "${GREEN}✓ Reusing Market Adapter at $MARKET_ADAPTER_URL${NC}"
fi

# Start API Server
echo -e "${BLUE}Starting FastAPI server on port $API_PORT...${NC}"
cd "$PROJECT_ROOT/livebench"
export AUTO_TRADER_STRATEGY_ID="${AUTO_TRADER_STRATEGY_ID:-clawwork-autotrader}"

# Create a Python script to start the server
python3 << 'EOF' > "$API_LOG" 2>&1 &
import os
import sys
sys.path.insert(0, os.getcwd())

# Set environment
os.environ["API_PORT"] = os.environ.get("API_PORT", "8001")

# Import and run
from api.server import app
import uvicorn

port = int(os.environ.get("API_PORT", 8001))
print(f"Starting FastAPI server on port {port}...")
uvicorn.run(app, host="0.0.0.0", port=port)
EOF

API_PID=$!
echo -e "${GREEN}✓ API Server PID: $API_PID${NC}"

# Wait for API to be ready
wait_for_service "http://localhost:$API_PORT/api/" "API Server" || exit 1

# Start Frontend
echo -e "${BLUE}Starting React frontend on port $FRONTEND_PORT...${NC}"
cd "$PROJECT_ROOT/frontend"

npm run dev -- --host 0.0.0.0 --port $FRONTEND_PORT > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
echo -e "${GREEN}✓ Frontend PID: $FRONTEND_PID${NC}"

# Wait for Frontend to be ready
wait_for_service "http://localhost:$FRONTEND_PORT/" "Frontend" || exit 1

# Start FYERS Screener Loop for AutoTrader market data
echo -e "${BLUE}Starting FYERS Screener Loop for AutoTrader...${NC}"
cd "$PROJECT_ROOT"
export VITE_API_URL="http://localhost:$API_PORT"
nohup bash -lc "cd '$PROJECT_ROOT' && while true; do echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] Running FYERS screener\"; bash './scripts/fyers_screener.sh'; status=\$?; if [[ \$status -ne 0 ]]; then echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] FYERS screener exited with status \$status\"; fi; sleep 15; done" >> "$SCREENER_LOG" 2>&1 &
SCREENER_PID=$!
echo -e "${GREEN}✓ FYERS Screener PID: $SCREENER_PID${NC}"

echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   ✓ All Systems Running                                   ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║   📊 Dashboard:    http://localhost:$FRONTEND_PORT           ║${NC}"
echo -e "${GREEN}║   🔧 API Server:   http://localhost:$API_PORT/api/          ║${NC}"
echo -e "${GREEN}║   📡 Adapter:      $MARKET_ADAPTER_URL                ║${NC}"
echo -e "${GREEN}║   📝 Logs:        $PROJECT_ROOT/logs/*.log  ║${NC}"
echo -e "${GREEN}║                                                            ║${NC}"
echo -e "${GREEN}║   Paper Trading Engine: AutoTrader                         ║${NC}"
echo -e "${GREEN}║   Strategy ID:         ${AUTO_TRADER_STRATEGY_ID}                           ║${NC}"
echo -e "${GREEN}║   Screener Feed:       ./scripts/fyers_screener.sh         ║${NC}"
echo -e "${GREEN}║   AutoTrader Control:  Dashboard or /api/auto-trader/*     ║${NC}"
echo -e "${GREEN}║                                                            ║${NC}"
echo -e "${GREEN}║   Press Ctrl+C to shutdown all services                   ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"

# Monitor logs and keep process alive
while true; do
  # Check if any process died
  for pid in $API_PID $FRONTEND_PID $SCREENER_PID; do
    if ! kill -0 $pid 2>/dev/null; then
      echo -e "${RED}✗ A process died (PID: $pid)${NC}"
      cleanup
    fi
  done
  
  sleep 5
done
