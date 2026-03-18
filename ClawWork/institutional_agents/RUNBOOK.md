# Institutional Agents Runbook (Safe Build Path)

This runbook keeps development isolated from live decision code.

## 1) Folder Contract
All new work must remain under:
- `institutional_agents/`

No direct edits to live trading paths until explicit integration phase.

## 2) Development Modes
- **Design mode**: specs/checklists only
- **Simulation mode**: replay historical data
- **Shadow mode**: run in parallel, no order action
- **Live mode**: enabled only after gates pass

## 3) Quality Gates
Before moving phase:
1. Schema validation passes
2. Backtest/paper metrics documented
3. Risk checks pass
4. Failure cases documented
5. Rollback path verified

## 4) Risk Rules (Non-Negotiable)
- Daily max loss cap enforced
- Per-trade max risk enforced
- Liquidity and spread guardrails enforced
- No trade on missing critical data

## 5) Suggested Weekly Rhythm
- Monday: define experiments and success criteria
- Tue–Thu: build and simulate
- Friday: review metrics, incidents, next actions

## 6) Change Log Template
Use this in every update:

## YYYY-MM-DD
- Phase:
- What changed:
- Why changed:
- Metrics before:
- Metrics after:
- Risk impact:
- Decision: keep / revert / iterate

## 7) Integration Readiness
Only integrate with current system when:
- Consistent improvement over baseline
- No critical risk violations for 2+ weeks in shadow mode
- Manual override and rollback tested
