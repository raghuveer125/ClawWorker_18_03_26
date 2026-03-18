# Market Adapter Service Checklist

Status as of 2026-03-12

- [x] Move live market access behind `shared_project_engine.market`
- [x] Centralize index aliasing and legacy runtime symbol fields
- [x] Add shared cross-process cache for quotes, history, and option chain
- [x] Add a single localhost market-data service process
- [x] Add service-side metrics for request counts, cache hits, cache misses, and duplicate suppression
- [x] Add a runtime report command for live duplicate metrics
- [x] Add a service-aware client with safe local fallback
- [x] Switch `pull_fyers_signal.py` to the shared market client
- [x] Switch `paper_trading_runner.py` to the shared market client
- [x] Make the shared launcher start the market-data service once
- [x] Make the ClawWork paper-trading launcher start or reuse the market-data service
- [x] Dry-verify compile, imports, and cache/service behavior without hitting live FYERS
- [x] Move LiveBench API market quote reads onto the shared market client
- [x] Move screener runtime quote reads onto the shared market client
- [x] Add strict launch mode to disable local market-data bypass in production
- [x] Add push transport for downstream consumers
- [x] Replace remaining non-live direct FYERS paths in backtesting and scaffolds
- [x] Add websocket transport if a persistent local streaming consumer is needed
- [x] Persist adapter metrics across restarts for closed-market verification
- [x] Add report export options for saved metric snapshots
- [x] Add a strict-mode audit command for launcher and client-path verification
- [x] Add closed-market smoke tests for service metrics and aggregated API endpoints
- [x] Add an open-market validation checklist/runbook
