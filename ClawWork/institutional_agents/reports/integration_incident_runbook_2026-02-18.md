# Integration Incident Runbook (On-call)

Date: 2026-02-18

## Trigger Conditions
- Adapter failure spike alert (`adapter_failure_rate_pct` > threshold)
- Schema mismatch alert (`schema_mismatch_count` > 0)
- Disagreement spike alert (`disagreement_spike_count` > 0)
- Any suspected live-risk behavior or unexpected routing

## Immediate Containment (Rollback to Baseline)
1. Set institutional adapter off:
   - `INSTITUTIONAL_ADAPTER_ENABLED=false`
2. Keep shadow mode safe default:
   - `INSTITUTIONAL_SHADOW_MODE=true`
3. Ensure live-order hard guards remain safe:
   - `FYERS_DRY_RUN=true`
   - `FYERS_ALLOW_LIVE_ORDERS=false`
4. Verify ClawWork continues baseline-only behavior.

## Verification Checklist
- Confirm screener responses still return baseline path.
- Confirm `institutional_shadow.status` is `disabled` or `failed_safe` (if errors occur).
- Confirm no increase in order attempts beyond baseline expectations.
- Confirm observability report returns `status: OK` post-containment.

## Evidence Collection
- Generate rollback report artifact via rollback drill runner.
- Capture latest observability report JSON/MD.
- Save incident timeline (start, containment, recovery timestamps).
- Record owner acknowledgements (Product, Risk, Engineering).

## Escalation Matrix
- **P1 (live-risk suspected):** page Risk + Engineering immediately, keep adapter disabled until signoff.
- **P2 (data quality / schema):** disable adapter, open hotfix ticket, rerun quality checks.
- **P3 (non-critical disagreement):** monitor and tune thresholds, no rollout promotion.

## Recovery Criteria
- Rollback artifact `passed: true`
- Observability report `status: OK`
- No unresolved high-severity alerts
- Product/Risk/Engineering approve next action
