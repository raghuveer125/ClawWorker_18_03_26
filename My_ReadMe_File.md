To login and verify use below code. 
Note: - We will add them in the start script.

# Login
python3 -m shared_project_engine.auth.cli login

# Status
python3 -m shared_project_engine.auth.cli status

# Test
python3 -m shared_project_engine.auth.cli test


After market:

./start.sh timeline-build 
./start.sh regime-detect 
./start.sh bot-rules-update



New Commands in start.sh
Command	Description
./start.sh all	Start everything: Market adapter + fyersN7 + Dashboard + Paper Trading + Scalping + LLM Debate
./start.sh scalping	Start 15-agent scalping system only
./start.sh llm-debate	Start LLM debate backend only
./start.sh both	Original command (without scalping/debate)
Market Hours (Updated)

8:58 AM  ─── Pre-market warmup starts
9:15 AM  ─── Market opens → Trading cycles (every 5s)
3:30 PM  ─── Market closes → Trading stops
3:40 PM  ─── Post-market learning → Shutdown
Auto-Schedule (Cron)
When you run ./start.sh all, it automatically installs:


# Mon-Fri at 8:58 AM
58 8 * * 1-5 cd /path/to/ClawWork_FyersN7 && ./start.sh all >> logs/startup.log 2>&1
To disable auto-install: SCALPING_CRON_AUTOINSTALL=0 ./start.sh all

Quick Start

# Start everything now
./start.sh all

# Or start components individually
./start.sh scalping     # Just scalping
./start.sh llm-debate   # Just debate backend
./start.sh both         # Original (no scalping)

# Stop all
./start.sh stop

# View logs
./start.sh logs
Live Trading (when ready)

# Enable live mode for scalping
SCALPING_LIVE=1 ./start.sh all





