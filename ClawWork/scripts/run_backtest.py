#!/usr/bin/env python3
"""
Run Backtest on Multi-Bot Ensemble Trading System

Usage:
    python scripts/run_backtest.py --days 30 --index NIFTY50
    python scripts/run_backtest.py --days 15 --all-indices
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

# Load .env file
env_file = project_root / ".env"
if env_file.exists():
    print(f"[Setup] Loading .env from: {env_file}")
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

# Now import and run
from livebench.backtesting.backtest import Backtester, main

if __name__ == "__main__":
    main()
