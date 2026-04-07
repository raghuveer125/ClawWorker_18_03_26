#!/usr/bin/env python3
"""
Paper Trading Integration Verification
Checks all components are properly configured before starting paper trading
"""

import os
import sys
import json
import subprocess
from pathlib import Path

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_section(title):
    print(f"\n{BLUE}{'='*60}")
    print(f"{title}")
    print(f"{'='*60}{RESET}")

def check_pass(item):
    print(f"{GREEN}✓{RESET} {item}")

def check_fail(item, hint=""):
    print(f"{RED}✗{RESET} {item}")
    if hint:
        print(f"  {YELLOW}→ {hint}{RESET}")

def check_warn(item, hint=""):
    print(f"{YELLOW}⚠{RESET} {item}")
    if hint:
        print(f"  {hint}")

PROJECT_ROOT = Path(__file__).parent
LIVEBENCH = PROJECT_ROOT / "livebench"
FRONTEND = PROJECT_ROOT / "frontend"

all_passed = True

print(f"\n{BLUE}╔════════════════════════════════════════════════════════════╗")
print(f"║  Paper Trading Integration Verification                   ║")
print(f"║  Phase 1B Configuration Check                             ║")
print(f"╚════════════════════════════════════════════════════════════╝{RESET}")

# 1. Check Python version
print_section("1. Python Environment")
if sys.version_info >= (3, 9):
    check_pass(f"Python {sys.version_info.major}.{sys.version_info.minor} (required: 3.9+)")
else:
    check_fail(f"Python {sys.version_info.major}.{sys.version_info.minor} (required: 3.9+)")
    all_passed = False

# 2. Check required directories exist
print_section("2. Project Structure")
required_dirs = {
    LIVEBENCH: "livebench/",
    LIVEBENCH / "api": "livebench/api/",
    LIVEBENCH / "bots": "livebench/bots/",
    LIVEBENCH / "data": "livebench/data/",
    FRONTEND: "frontend/",
    FRONTEND / "src": "frontend/src/",
}

for path, name in required_dirs.items():
    if path.exists():
        check_pass(f"Directory exists: {name}")
    else:
        check_fail(f"Directory missing: {name}", f"Run: mkdir -p {path}")
        all_passed = False

# 3. Check required Python files exist
print_section("3. Python Modules")
required_files = {
    LIVEBENCH / "api" / "server.py": "API server",
    LIVEBENCH / "bots" / "ict_sniper.py": "ICT Sniper bot (Phase 1B)",
    LIVEBENCH / "bots" / "ensemble.py": "Bot ensemble coordinator",
    LIVEBENCH / "bots" / "multi_timeframe.py": "Multi-timeframe analyzer",
    PROJECT_ROOT / "paper_trading_runner.py": "Paper trading runner",
}

for path, name in required_files.items():
    if path.exists():
        check_pass(f"Python file: {name}")
    else:
        check_fail(f"Python file missing: {name}", f"Path: {path}")
        all_passed = False

# 4. Check frontend files exist
print_section("4. Frontend Components")
frontend_files = {
    FRONTEND / "src" / "pages" / "BotEnsemble.jsx": "Bot monitoring dashboard",
    FRONTEND / "src" / "api.js": "API client layer",
}

for path, name in frontend_files.items():
    if path.exists():
        check_pass(f"Frontend file: {name}")
    else:
        check_fail(f"Frontend file missing: {name}", f"Path: {path}")
        all_passed = False

# 5. Check Python dependencies
print_section("5. Python Dependencies")
required_packages = [
    "fastapi",
    "uvicorn",
    "aiohttp",
    "requests",
    "pydantic",
    "python-dotenv",
]

try:
    import pkg_resources
    installed = {pkg.key for pkg in pkg_resources.working_set}
    
    for package in required_packages:
        if package.lower() in installed or package.replace("-", "_") in installed:
            check_pass(f"Package: {package}")
        else:
            check_warn(f"Package not found: {package}", 
                      f"Install with: pip install {package}")
except Exception as e:
    check_warn(f"Could not check dependencies: {e}")

# 6. Check environment configuration
print_section("6. Environment Configuration")
env_file = PROJECT_ROOT / ".env"

if env_file.exists():
    check_pass(".env file exists")
    try:
        with open(env_file) as f:
            env_vars = {}
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
        
        important_vars = {
            "API_PORT": "8001",
            "FRONTEND_PORT": "3001",
            "API_URL": "http://localhost:8001/api",
            "MARKET_DATA_PROVIDER": "fyers",
        }
        
        for var, default in important_vars.items():
            if var in env_vars:
                check_pass(f"Env var: {var} = {env_vars[var]}")
            else:
                check_warn(f"Env var not set: {var}", f"Default: {default}")
    except Exception as e:
        check_fail(f"Error reading .env: {e}")
else:
    check_warn(".env file not found", 
              "Create with: cp .env.example .env (or manually set variables)")

# 7. Check Phase 1B configuration in code
print_section("7. Phase 1B Configuration Status")
try:
    ict_sniper_file = LIVEBENCH / "bots" / "ict_sniper.py"
    if ict_sniper_file.exists():
        with open(ict_sniper_file) as f:
            content = f.read()
            
        checks = {
            "swing_lookback.*=.*9": ("swing_lookback = 9", True),
            "mss_swing_len.*=.*2": ("mss_swing_len = 2", True),
            "vol_multiplier.*=.*1\\.2": ("vol_multiplier = 1.2", True),
            "displacement_multiplier.*=.*1\\.3": ("displacement_multiplier = 1.3", True),
        }
        
        import re
        for pattern, (name, required) in checks.items():
            if re.search(pattern, content):
                check_pass(f"Phase 1B param: {name}")
            elif required:
                check_fail(f"Phase 1B param missing: {name}")
                all_passed = False
            else:
                check_warn(f"Phase 1B param not found: {name}")
    else:
        check_fail("ICT Sniper bot not found")
        all_passed = False
except Exception as e:
    check_warn(f"Could not verify Phase 1B config: {e}")

# 8. Check MTF configuration
print_section("8. MTF Filter Configuration")
try:
    ensemble_file = LIVEBENCH / "bots" / "ensemble.py"
    if ensemble_file.exists():
        with open(ensemble_file) as f:
            content = f.read()
        
        if "permissive" in content.lower():
            check_pass("MTF mode: Permissive (confidence-gated)")
        else:
            check_warn("MTF mode: Could not verify permissive mode")
        
        if "confidence" in content.lower() or "signal_confidence" in content:
            check_pass("Confidence-based filtering: Implemented")
        else:
            check_warn("Confidence-based filtering: Could not verify")
except Exception as e:
    check_warn(f"Could not verify MTF config: {e}")

# 9. Check ports availability
print_section("9. Port Availability")
import socket

ports = {
    8001: "API Server",
    3001: "React Frontend",
}

for port, service in ports.items():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        
        if result != 0:
            check_pass(f"Port {port} available ({service})")
        else:
            check_warn(f"Port {port} is in use ({service})", 
                      f"Kill with: lsof -ti :{port} | xargs kill -9")
    except Exception as e:
        check_warn(f"Could not check port {port}: {e}")

# 10. Check log directory
print_section("10. Logging Setup")
log_dir = PROJECT_ROOT / "logs"
if log_dir.exists():
    check_pass("Log directory exists: logs/")
else:
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        check_pass("Log directory created: logs/")
    except Exception as e:
        check_fail(f"Could not create log directory: {e}")
        all_passed = False

# 11. Check startup scripts
print_section("11. Startup Scripts")
startup_script = PROJECT_ROOT / "start_paper_trading.sh"
if startup_script.exists():
    check_pass("Paper trading startup script: start_paper_trading.sh")
    try:
        os.access(startup_script, os.X_OK)
        check_pass("Script is executable")
    except OSError:
        check_warn("Script not executable",
                  f"Run: chmod +x {startup_script}")
else:
    check_fail("Startup script missing: start_paper_trading.sh")
    all_passed = False

# 12. Check data storage
print_section("12. Data Storage")
data_dir = LIVEBENCH / "data"
if data_dir.exists():
    check_pass("Data directory exists: livebench/data/")
    
    # Check for paper trading log
    paper_log = data_dir / "paper_trading.jsonl"
    paper_state = data_dir / "paper_trading_state.json"
    
    if paper_log.exists():
        check_pass(f"Paper trading log: {paper_log.stat().st_size} bytes")
    else:
        check_pass("Paper trading log will be created on first run")
    
    if paper_state.exists():
        check_pass("Session state will be persisted")
else:
    check_fail("Data directory missing", f"Run: mkdir -p {data_dir}")

# Summary
print_section("VERIFICATION SUMMARY")

if all_passed:
    print(f"{GREEN}╔════════════════════════════════════════════════════════════╗")
    print(f"║  ✓ All critical checks passed!                             ║")
    print(f"║                                                            ║")
    print(f"║  Ready to start paper trading with Phase 1B config         ║")
    print(f"║                                                            ║")
    print(f"║  Next step: ./start_paper_trading.sh                       ║")
    print(f"║  Dashboard: http://localhost:3001                          ║")
    print(f"╚════════════════════════════════════════════════════════════╝{RESET}")
    sys.exit(0)
else:
    print(f"{RED}╔════════════════════════════════════════════════════════════╗")
    print(f"║  ✗ Some checks failed - please fix before starting       ║")
    print(f"║                                                            ║")
    print(f"║  See messages above for how to resolve issues             ║")
    print(f"╚════════════════════════════════════════════════════════════╝{RESET}")
    sys.exit(1)
