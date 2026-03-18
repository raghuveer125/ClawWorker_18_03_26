# ClawWork Dashboard - Quick Start

## One Command Startup

To bring up the entire application (backend, frontend, tunnel, screener):

```bash
cd /path/to/ClawWork_FyersN7/ClawWork && ./start_dashboard.sh
```

## What It Does

The startup script automatically:
1. Runs FYERS auto-authentication (opens browser for login)
2. Starts Backend API on port 8000
3. Starts Frontend Dashboard on port 3000
4. Starts Cloudflare Tunnel for remote access
5. Starts FYERS screener loop (30s interval)

## Access URLs

| Service | URL |
|---------|-----|
| Local Dashboard | http://localhost:3000 |
| Remote Dashboard | https://trading.bhoomidaksh.xyz |
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

## Optional Flags

```bash
# Skip FYERS authentication (if token is still valid)
SKIP_AUTH=1 ./start_dashboard.sh

# Disable screener loop
SCREENER_ENABLED=0 ./start_dashboard.sh

# Combine flags
SKIP_AUTH=1 SCREENER_ENABLED=0 ./start_dashboard.sh
```

## View Logs

```bash
tail -f logs/api.log       # Backend API logs
tail -f logs/frontend.log  # Frontend logs
tail -f logs/tunnel.log    # Cloudflare tunnel logs
tail -f logs/screener.log  # FYERS screener logs
```

## Stop All Services

Press `Ctrl+C` in the terminal running the script.
