# Open Market Validation Checklist

Use this after the closed-market implementation has already been verified.

## Before Market Open

- [ ] Confirm auth is valid:
  - `bash shared_project_engine/launcher/start.sh status`
- [ ] Start the full stack:
  - `DETACH_AFTER_START=1 bash shared_project_engine/launcher/start.sh both`
- [ ] Confirm adapter health:
  - `curl http://127.0.0.1:8765/health`
- [ ] Capture a baseline report before traffic:
  - `bash shared_project_engine/launcher/start.sh market-report --write-text logs/market_report_preopen.txt --write-json logs/market_report_preopen.json`
- [ ] Run the strict-mode audit:
  - `bash shared_project_engine/launcher/start.sh market-audit`

## During Market Hours

- [ ] Let the stack run under normal usage for at least 30 minutes.
- [ ] Open the main dashboards that consume market data:
  - dashboard
  - bots
  - signals
  - swing analysis if needed
- [ ] If multiple browser sessions are expected in production, open more than one session and confirm no unexpected adapter spike.
- [ ] Capture periodic reports:
  - `bash shared_project_engine/launcher/start.sh market-report --write-text logs/market_report_1030.txt --write-json logs/market_report_1030.json`
  - `bash shared_project_engine/launcher/start.sh market-report --write-text logs/market_report_1130.txt --write-json logs/market_report_1130.json`
  - `bash shared_project_engine/launcher/start.sh market-report --write-text logs/market_report_close.txt --write-json logs/market_report_close.json`

## Metrics To Review

- [ ] Total local adapter requests
- [ ] Total upstream FYERS fetches
- [ ] Total duplicate requests suppressed
- [ ] Cache hit rate
- [ ] Endpoint summary:
  - quotes
  - future_quote
  - history
  - option_chain
  - any snapshot endpoints
- [ ] Top duplicate keys
- [ ] Active quote streams and subscribers

## Behavioral Checks

- [ ] `SignalView` should show one FYERSN7 snapshot request per refresh cycle, not three separate requests.
- [ ] `Dashboard` supplemental refresh should use one combined request, not three separate polling calls.
- [ ] `SwingAnalysis` live mode should use one `live-signals` request per cycle, not `dates + signals`.
- [ ] `BotEnsemble` should keep the fast/slow split and avoid learning ROI in the 10s loop.
- [ ] Hidden browser tabs should not continue fallback polling.

## After Market Close

- [ ] Export a final report:
  - `bash shared_project_engine/launcher/start.sh market-report --write-text logs/market_report_final.txt --write-json logs/market_report_final.json`
- [ ] Compare pre-open and final totals.
- [ ] Record the top duplicate keys that still remain.
- [ ] Decide whether any remaining intentional pollers should move to stream-first updates.

## Expected Outcome

- Adapter totals should show real duplicate suppression under live usage.
- Upstream FYERS fetches should be materially lower than raw local request totals.
- The remaining traffic should come mostly from intentional polling or live analysis, not duplicate fanout.
