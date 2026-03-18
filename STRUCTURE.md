# Project Structure

This document describes the reorganized structure of ClawWork_FyersN7.

## Directory Layout

```
ClawWork_FyersN7/
├── .env                      # Single source of truth for credentials
├── config/                   # Centralized configuration (future)
├── engines/                  # All trading engines
│   ├── scalping/            # 18-agent scalping system (was bot_army/)
│   │   ├── bots/            # Bot implementations
│   │   ├── scalping/        # Scalping-specific agents
│   │   ├── orchestrator/    # Bot coordination
│   │   └── integrations/    # External integrations (Fyers, LLM debate)
│   └── fyersN7/             # FyersN7 signal engine (25/15 strategy)
│       └── fyers-2026-03-05/ # Active strategy version
├── ai_hub/                   # AI infrastructure library
│   ├── layer0/              # Data adapters & pipes (ACTIVE)
│   │   └── adapters/        # FyersN7SignalAdapter, etc.
│   ├── layer6/              # Genetic optimization (ACTIVE)
│   │   └── genetic/         # Strategy evolution
│   └── layer1-5, synapse/   # Future expansion (scaffolded)
├── ClawWork/                 # Main application
│   ├── livebench/           # Core trading logic
│   │   ├── api/             # REST API server
│   │   ├── bots/            # Trading bot strategies
│   │   ├── backtesting/     # Backtest framework
│   │   └── trading/         # Order execution
│   ├── frontend/            # React dashboard
│   ├── institutional_agents/ # Phase 1-5 institutional framework
│   └── logs -> ../logs      # Symlink to root logs
├── shared_project_engine/    # Shared infrastructure
│   ├── auth/                # Fyers authentication
│   ├── indices/             # Index configuration (NIFTY, BANKNIFTY, etc.)
│   ├── market/              # Market data client
│   ├── services/            # Port & URL configuration
│   ├── trading/             # Trade execution
│   └── launcher/            # start.sh and related scripts
├── llm_debate/              # LLM debate system (standalone)
│   ├── backend/             # Python FastAPI server
│   └── frontend/            # React UI
├── logs/                    # Centralized log directory
└── bot_army -> engines/scalping  # Backward compatibility symlink
```

## Key Principles

1. **Engines in `engines/`**: All trading engines live under `engines/`
   - `scalping/` - 18-agent high-frequency scalping
   - `fyersN7/` - Signal-based trading with 25/15 exit strategy

2. **Backward Compatibility**: Symlinks preserve old import paths
   - `bot_army` -> `engines/scalping`
   - `fyersN7` -> `engines/fyersN7`
   - `ClawWork/logs` -> `logs`

3. **Centralized Logs**: All logs in root `logs/` directory

4. **Single .env**: Root `.env` is the source of truth for credentials

## Port Configuration

| Service          | Port | Source                          |
|-----------------|------|----------------------------------|
| API Server      | 8001 | shared_project_engine/services/ |
| Frontend        | 3001 | shared_project_engine/services/ |
| Market Adapter  | 8765 | shared_project_engine/services/ |
| FyersN7 Web     | 8787 | fyersN7 LIVE_PORT               |
| Scalping API    | 8082 | start.sh SCALPING_API_PORT      |
| LLM Debate      | 8080 | llm_debate default              |
| Auth Callback   | 8080 | shared_project_engine/services/ |

## Quick Start

```bash
# Start all services
./shared_project_engine/launcher/start.sh all

# Or from ClawWork
cd ClawWork && ./start.sh all
```

## Migration Notes (2026-03-15)

- Moved `bot_army/` to `engines/scalping/`
- Moved `fyersN7/` to `engines/fyersN7/`
- Removed corrupted `ClawWork/bot_army/`
- Consolidated logs to root `logs/`
- Cleaned up `tmp_*` and `__pycache__` directories
