# Phase 0: Acceptance Metrics (MVP → Institutional)

Status: Draft v1  
Owner: Product + Risk + Engineering  
Date: 2026-02-18

## 1) Objective
Define measurable gates to move from one phase to the next safely.

## 2) Data Quality Metrics
- Required field completeness: `>= 95%`
- Null critical fields in decision payload: `0`
- Invalid schema responses: `0`

## 3) Reliability Metrics
- Decision pipeline success rate: `>= 99%`
- Unhandled runtime exceptions: `0` in validation window
- Mean decision latency: target to be defined (track from day 1)

## 4) Strategy Quality Metrics (Paper/Shadow)
- Signal precision (directional): baseline + target to be set
- Hit rate: baseline + target to be set
- Risk-adjusted return (e.g., Sharpe-like proxy): improve vs baseline
- False signal rate: reduce vs Phase 1 baseline

## 5) Risk Metrics (Must Pass)
- Max drawdown in validation window: within approved limit
- Daily loss cap breach count: `0`
- Trades without stop-loss: `0`
- Policy veto bypass count: `0`

## 6) Operational Metrics
- Monitoring coverage for critical checks: `100%`
- Alert delivery test pass rate: `100%`
- Rollback drill success: `100%`

## 7) Phase Gates

### Gate A: Phase 0 → Phase 1
- [ ] Schema document approved
- [ ] Risk policy approved
- [ ] Acceptance metrics approved

### Gate B: Phase 1 → Phase 2
- [ ] Baseline report complete
- [ ] No critical reliability failures
- [ ] Risk metrics pass

### Gate C: Phase 2 → Phase 3
- [ ] Options engine improves risk-adjusted metrics vs baseline
- [ ] False signal rate reduced
- [ ] No guardrail violations in test window

### Gate D: Phase 3 → Integration
- [ ] Multi-agent consensus stability validated
- [ ] Shadow-mode report for 2+ weeks completed
- [ ] Rollback and incident runbook tested

## 8) Reporting Template (Per Cycle)
- Date range:
- Phase:
- Baseline reference:
- Metrics summary:
- Incidents:
- Decision (pass/fail):
- Next actions:

## 9) Sign-off
- [ ] Product owner sign-off
- [ ] Risk owner sign-off
- [ ] Engineering owner sign-off
