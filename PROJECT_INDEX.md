# ClawWork_FyersN7 Integration Index

## Directory Structure
```
ClawWork_FyersN7/
├── ClawWork/          (Primary - AI Trading System)
└── fyersN7/           (To Integrate - FYERS Signal System)
    └── fyers-2026-03-05/
```

---

## Project 1: ClawWork (Primary)

### Purpose
AI-powered multi-bot trading system with ensemble strategies, institutional analysis, ICT concepts, and paper trading capabilities.

### Key Directories
| Directory | Purpose |
|-----------|---------|
| `livebench/` | Main trading engine |
| `livebench/bots/` | 20+ trading bots (ensemble, ICT, ML, momentum, etc.) |
| `livebench/trading/` | Trade execution logic |
| `livebench/configs/` | Bot configuration files |
| `livebench/data/` | Trading data, positions, insights |
| `institutional_agents/` | Institutional flow analysis |
| `frontend/` | Dashboard UI |
| `scripts/` | Utility scripts |

### Core Trading Bots (livebench/bots/)
| Bot | Purpose |
|-----|---------|
| `ensemble.py` | Main ensemble orchestrator (103KB) |
| `ict_sniper.py` | ICT concepts implementation |
| `ml_bot.py` | Machine learning signals |
| `institutional_risk_layer.py` | Risk management |
| `adaptive_risk_controller.py` | Dynamic risk adjustment |
| `capital_allocator.py` | Position sizing |
| `execution_engine.py` | Order execution |
| `multi_timeframe.py` | MTF analysis |
| `deep_learning.py` | DL models |
| `regime_detector.py` | Market regime detection |
| `momentum_scalper.py` | Momentum strategies |
| `reversal_hunter.py` | Mean reversion |
| `trend_follower.py` | Trend following |
| `volatility_trader.py` | Volatility plays |

### Entry Points
| File | Purpose |
|------|---------|
| `paper_trading_runner.py` | Paper trading entry |
| `start_paper_trading.sh` | Paper trading launcher |
| `start_dashboard.sh` | Dashboard launcher |
| `livebench/main.py` | Main livebench entry |

### Config & Environment
- `.env` - API keys (FYERS, OpenAI, etc.)
- `livebench/configs/` - Bot parameters

---

## Project 2: fyersN7 (To Integrate)

### Purpose
FYERS API-based signal generation system with FIA (Financial Intelligence Advisor) validation, opportunity detection, and paper trading.

### Location
`fyersN7/fyers-2026-03-05/`

### Key Scripts (scripts/)
| Script | Purpose |
|--------|---------|
| `pull_fyers_signal.py` (72KB) | Main signal puller with voting engine |
| `opportunity_engine.py` (34KB) | Entry/exit opportunity detection |
| `paper_trade_loop.py` (28KB) | Paper trading execution |
| `add_fia_signal.py` | FIA signal validation |
| `fyers_auth.py` | FYERS authentication |
| `generate_live_signal_view.py` | Live signal HTML view |
| `update_adaptive_model.py` | Self-improving ML model |
| `analyze_paper_trades.py` | Trade analysis |

### Launcher Scripts
| Script | Purpose |
|--------|---------|
| `start_all.sh` | Master launcher |
| `run_signal_loop.sh` | Signal loop |
| `run_opportunity_engine.sh` | Opportunity engine |
| `run_paper_trade_loop.sh` | Paper trade loop |
| `run_two_engines.sh` | Both engines together |

### Data Files
| File | Purpose |
|------|---------|
| `signals.csv` | Signal history |
| `decision_journal.csv` | ML training data |
| `paper_trades.csv` | Paper trade log |
| `paper_equity.csv` | Equity curve |
| `opportunity_events.csv` | Opportunity events |
| `.opportunity_engine_state_*.json` | Per-index state |
| `.paper_trade_state_*.json` | Per-index paper state |

### Multi-Index Support
- NIFTY50, BANKNIFTY, SENSEX (separate state files per index)

### Features
- FIA signal validation with quality scoring
- Vote-based side decision (CE/PE)
- Greeks: Delta, Gamma, Theta, Vega, IV
- Adaptive learning (self-improving model)
- Reversal detection with OI flow
- Spread tracking and filtering
- Context-aware filters (VIX, PCR, Max Pain, Basis)

---

## Integration Analysis

### Overlap Areas
| Area | ClawWork | fyersN7 |
|------|----------|---------|
| Paper Trading | `paper_trading_runner.py` | `paper_trade_loop.py` |
| Signal Generation | `ensemble.py` + bots | `pull_fyers_signal.py` |
| Risk Management | `institutional_risk_layer.py` | Opportunity engine |
| Adaptive Learning | `adaptive_optimizer.py` | `update_adaptive_model.py` |
| FYERS API | Via livebench | Direct via fyers-apiv3 |

### Complementary Features
| fyersN7 Has | ClawWork Could Use |
|-------------|-------------------|
| FIA validation logic | Signal quality gating |
| Vote engine (CE/PE) | Side decision support |
| Reversal detection | Exit signal enhancement |
| OTM ladder scanning | Strike selection |
| Spread tracking | Execution quality |

| ClawWork Has | fyersN7 Could Use |
|--------------|-------------------|
| 20+ ensemble bots | More signal sources |
| ICT concepts | Advanced entry timing |
| Institutional flow | Context enrichment |
| ML/DL models | Better predictions |
| Risk layering | Position management |

### Potential Integration Points
1. **Signal Pipeline**: fyersN7 signals -> ClawWork ensemble
2. **Execution**: ClawWork bots use fyersN7 strike selection
3. **Validation**: FIA quality check before ClawWork trades
4. **State Sync**: Unified paper trading state
5. **Adaptive**: Merge learning loops

---

## Quick Start Commands

### ClawWork
```bash
cd /path/to/ClawWork_FyersN7/ClawWork
./start_paper_trading.sh
```

### fyersN7
```bash
cd /path/to/ClawWork_FyersN7/fyersN7/fyers-2026-03-05
scripts/start_all.sh run
```

---

## Next Steps
Tell me which integration approach you want to start with:
1. Feed fyersN7 signals into ClawWork ensemble
2. Use ClawWork bots as signal source for fyersN7 execution
3. Unified paper trading system
4. Merge adaptive learning systems
5. Other specific integration
