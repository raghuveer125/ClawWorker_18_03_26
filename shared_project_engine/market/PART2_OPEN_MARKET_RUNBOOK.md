# Part 2: Open Market Execution Runbook

Use this file when the market is open and you want me to perform live validation.

This runbook assumes Part 1 is already complete and committed.

Related reference:
- `shared_project_engine/market/OPEN_MARKET_VALIDATION_CHECKLIST.md`

## Objective

Measure real live-market behavior after the adapter consolidation work:
- actual upstream FYERS call volume
- actual duplicate suppression
- actual cache hit rate
- top duplicate keys under live traffic
- whether any remaining intentional pollers need more reduction

## What I Should Do When You Ask

When you tell me to execute Part 2, I should do this in order:

1. Verify auth and service health
2. Start or reuse the live stack
3. Capture a pre-run adapter report
4. Let the system run during market hours
5. Capture interval reports
6. Summarize real duplicate behavior
7. Recommend only data-backed tuning changes

## Preconditions

Before running Part 2, all of these should be true:
- market is open
- FYERS auth is valid
- network access is available
- the adapter host is reachable on `127.0.0.1:8765`

## Commands

### 1. Auth check

```bash
bash shared_project_engine/launcher/start.sh status
```

If auth is invalid:

```bash
bash shared_project_engine/launcher/start.sh login
```

### 2. Start the live stack

```bash
DETACH_AFTER_START=1 bash shared_project_engine/launcher/start.sh both
```

### 3. Strict-mode audit

```bash
bash shared_project_engine/launcher/start.sh market-audit
```

Expected:
- `Status: ok`

### 4. Pre-run report

```bash
bash shared_project_engine/launcher/start.sh market-report --top 20 --write-text logs/market_report_preopen.txt --write-json logs/market_report_preopen.json
```

### 5. Live observation window

Minimum observation time:
- 30 minutes

Preferred observation time:
- 60 minutes

During this time I should:
- keep the dashboard running
- open the pages that consume market data
- include normal user behavior if possible
- avoid synthetic spam unless explicitly requested

### 6. Interval reports

Capture at least:

```bash
bash shared_project_engine/launcher/start.sh market-report --top 20 --write-text logs/market_report_t1.txt --write-json logs/market_report_t1.json
```

```bash
bash shared_project_engine/launcher/start.sh market-report --top 20 --write-text logs/market_report_t2.txt --write-json logs/market_report_t2.json
```

Final report:

```bash
bash shared_project_engine/launcher/start.sh market-report --top 20 --write-text logs/market_report_final.txt --write-json logs/market_report_final.json
```

## Metrics I Should Report Back

I should summarize:
- total local adapter requests
- total upstream FYERS fetches
- total duplicate requests suppressed
- overall cache hit rate
- session-local totals if the adapter restarted during the run
- endpoint-level totals:
  - `quotes`
  - `future_quote`
  - `history`
  - `option_chain`
  - any other active endpoint
- top duplicate keys
- active quote stream/subscriber behavior if present

## Behavioral Checks

I should explicitly confirm:
- `SignalView` is still one snapshot request per refresh cycle
- `Dashboard` supplemental refresh stays collapsed to one request
- `SwingAnalysis` live mode uses one `live-signals` request per cycle
- `BotEnsemble` keeps the fast/slow polling split
- hidden tabs are not generating fallback polling

## Decision Rules

Only propose further changes if live data shows a real problem.

Good outcome:
- upstream FYERS fetches are materially lower than local request count
- duplicate suppression is significant
- top duplicate keys are expected shared datasets
- no unexpected API spike from any page

Needs follow-up:
- one endpoint dominates upstream fetches unexpectedly
- duplicate suppression is low where shared reuse should exist
- a UI page causes disproportionate request volume
- cache TTL is too short under real usage

## If Tuning Is Needed

Possible next actions after live evidence:
- adjust adapter TTLs
- move another page from polling to stream-first behavior
- add persistence or aggregation for one remaining high-volume endpoint
- change report granularity if a duplicate pattern is not visible enough

## Deliverables I Should Produce

When you ask me to run Part 2, my final response should include:
- a concise live summary
- exact counts from the final report
- top duplicate keys
- whether the architecture held under market load
- whether new code changes are justified or not

## Trigger Phrase

When market opens, you can tell me:

`Read PART2_OPEN_MARKET_RUNBOOK.md and execute Part 2.`
