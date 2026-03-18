#!/bin/bash
# LLM Debate System - Startup Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "   LLM Debate System - Starting..."
echo "=========================================="

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required"
    exit 1
fi

# Check for Node.js
if ! command -v npm &> /dev/null; then
    echo "Error: Node.js/npm is required"
    exit 1
fi

# Setup backend
echo ""
echo "[1/4] Setting up Python backend..."
cd backend

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt

cd ..

# Setup frontend
echo ""
echo "[2/4] Setting up React frontend..."
cd frontend

if [ ! -d "node_modules" ]; then
    echo "Installing npm packages..."
    npm install
fi

cd ..

# Start backend
echo ""
echo "[3/4] Starting backend server on port 8080..."
cd backend
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8080 &
BACKEND_PID=$!
cd ..

# Wait for backend
sleep 2

# Start frontend
echo ""
echo "[4/4] Starting frontend dev server on port 3002..."
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "=========================================="
echo "   LLM Debate System is running!"
echo "=========================================="
echo ""
echo "   Frontend: http://localhost:3002"
echo "   Backend:  http://localhost:8080"
echo ""
echo "   Press Ctrl+C to stop all services"
echo "=========================================="

# Cleanup on exit
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait
wait
