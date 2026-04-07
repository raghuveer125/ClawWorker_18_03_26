# EOE Session Report — <DATE>

*Template v1.0 — Fill mechanically from shadow logs. No interpretation.*

---

## 1. SESSION SUMMARY

| Field | Value |
|-------|-------|
| Date | YYYY-MM-DD |
| Expiry type | weekly_nse / weekly_bse |
| Index | BSE:SENSEX-INDEX / NSE:NIFTY50-INDEX |
| Open | ₹ |
| High | ₹ |
| Low | ₹ |
| Close | ₹ |
| Gap % | (open - prev_close) / prev_close × 100 |
| Day range % | (high - low) / low × 100 |
| Morning trend direction | bearish / bullish / flat |
| Morning trend magnitude % | (open - extreme) / open × 100 |
| Reversal occurred | YES if price reversed ≥ 1.5% from extreme / NO |
| Reversal magnitude % | (close - extreme) / extreme × 100 |
| Convex option expansion occurred | YES if any tracked CE/PE had MFE ≥ 5x from session low / NO |
| Session classification | trending_continuation / failed_reversal / mild_reversal / strong_reversal / choppy |
| Session usable for v1/v2 comparison | YES / NO (state reason if NO) |
| Unusable reason (if any) | data_gap / no_morning_trend / non_expiry / system_error |

---

## 2. V1 REPORT

### 2a. State Transitions

| # | Time | From | To | Trigger | LTP |
|---|------|------|----|---------|-----|
| 1 | | | | | |

### 2b. Activation Metrics

| Metric | Value |
|--------|-------|
| ARMED reached | YES / NO |
| ARMED time | HH:MM:SS |
| ACTIVE reached | YES / NO |
| ACTIVE time | HH:MM:SS |
| ACTIVE duration (min) | |
| True activation | YES / NO / N/A |
| False activation | YES / NO / N/A |

**True activation test**: spot at ACTIVE_time + 30 min ≥ 0.3% further in reversal direction
**False activation test**: spot at ACTIVE_time + 15 min ≥ 0.3% against reversal direction

### 2c. Hypothetical Trades

| # | Strike | Type | Entry ₹ | Entry Time | Peak ₹ | Exit ₹ | Exit Time | Exit Reason | Payoff | MFE | MAE | Spread Trap |
|---|--------|------|---------|-----------|--------|--------|-----------|-------------|--------|-----|-----|-------------|
| 1 | | | | | | | | | x | x | x | Y/N |

### 2d. Failure Modes

| Mode | Triggered | Detail |
|------|-----------|--------|
| F1: Fake reversal | Y/N | |
| F2: Spread trap | Y/N | |
| F4: Late activation | Y/N | |
| F5: Stuck ARMED | Y/N | |
| F7: Theta decay | Y/N | |

### 2e. Session Metrics

| Metric | Value |
|--------|-------|
| Trades taken | |
| Win rate | |
| Expectancy (₹) | |
| Max loss (₹) | |
| Tradable rate (%) | |
| MFE >3x rate (%) | |

---

## 3. V2 REPORT

### 3a. Compression Detection

| Strike | Open ₹ | Low ₹ | Compression % | Time of Low | Compressed (≥90%) |
|--------|--------|-------|---------------|-------------|-------------------|
| | | | | | YES / NO |

### 3b. Revival Detection

| Strike | Low ₹ | Revival Start ₹ | Revival % | Consecutive Higher Closes | Reviving |
|--------|-------|-----------------|-----------|--------------------------|----------|
| | | | | | YES / NO |

### 3c. State Transitions

| # | Time | From | To | Trigger | Premium at Trigger ₹ |
|---|------|------|----|---------|---------------------|
| 1 | | | | | |

### 3d. Activation Metrics

| Metric | Value |
|--------|-------|
| ARMED reached | YES / NO |
| ARMED time | HH:MM:SS |
| ARMED trigger | compression ≥ 90% + first HH |
| ACTIVE reached | YES / NO |
| ACTIVE time | HH:MM:SS |
| ACTIVE trigger | 3 higher closes + revival ≥ 30% |
| ACTIVE duration (min) | |
| True activation (v2) | YES / NO / N/A |
| False activation (v2) | YES / NO / N/A |

**True activation test (v2)**: premium at ACTIVE_time + 30 min ≥ 2x premium at trigger
**False activation test (v2)**: premium at ACTIVE_time + 15 min < 0.8x premium at trigger

### 3e. Hypothetical Trades

| # | Strike | Type | Entry Method | Entry ₹ | Entry Time | Compression at Entry | Revival at Entry | Time from Low (min) | Peak ₹ | Exit ₹ | Exit Reason | Payoff | MFE | MAE | Spread Trap |
|---|--------|------|-------------|---------|-----------|---------------------|-----------------|--------------------|----|-----|-------------|--------|-----|-----|-------------|
| 1 | | | v2_earliest / v2_confirmed / v2_late | | | % | % | | | | | x | x | x | Y/N |

### 3f. Failure Modes (v2-specific)

| Mode | Triggered | Detail |
|------|-----------|--------|
| FP1: Compression without revival | Y/N | |
| FP2: Revival without expansion | Y/N | |
| FP3: Noise bounce | Y/N | |
| FP4: Illiquid spike | Y/N | |
| FP5: Theta decay | Y/N | |

### 3g. Session Metrics

| Metric | Value |
|--------|-------|
| Compressions detected | |
| Revivals detected | |
| Trades taken | |
| Win rate | |
| Expectancy (₹) | |
| Max loss (₹) | |
| Tradable rate (%) | |
| MFE >3x rate (%) | |

---

## 4. SIDE-BY-SIDE COMPARISON

| Metric | v1 | v2 | Better |
|--------|----|----|--------|
| Time to ARMED | HH:MM | HH:MM | Earlier (if true) |
| Time to ACTIVE | HH:MM | HH:MM | Earlier (if true) |
| True activation | YES/NO | YES/NO | YES > NO |
| False activation | YES/NO | YES/NO | NO > YES |
| Trades enabled | N | N | More (if quality maintained) |
| Best entry payoff potential | Xx | Xx | Higher |
| Tradable at best entry | Y/N | Y/N | YES > NO |
| Late activation | Y/N | Y/N | NO > YES |
| Expectancy (₹) | ₹ | ₹ | Higher |
| Failure modes triggered | N | N | Fewer |
| MFE from entry | Xx | Xx | Higher |

### Scoring

Each row scored: +1 for winner, 0 for tie, -1 for loser.

| Metric | v1 Score | v2 Score |
|--------|---------|---------|
| Time to ARMED | | |
| Time to ACTIVE | | |
| True activation | | |
| False activation | | |
| Trades enabled | | |
| Entry payoff | | |
| Tradable | | |
| Late activation | | |
| Expectancy | | |
| Failure modes | | |
| MFE | | |
| **TOTAL** | **/11** | **/11** |

---

## 5. SESSION VERDICT

### Rules (mechanical, no interpretation)

```
IF v2_total > v1_total + 2:
    verdict = "v2_better"
ELIF v1_total > v2_total + 2:
    verdict = "v1_better"
ELIF abs(v1_total - v2_total) <= 2:
    verdict = "tie"

IF neither activated AND no reversal occurred:
    verdict = "both_inactive_no_opportunity"
    counts_toward_decision = YES (validates non-activation behavior)

IF neither activated AND reversal DID occur:
    verdict = "both_missed"
    counts_toward_decision = YES (both failed)

IF session_usable == NO:
    verdict = "unusable"
    counts_toward_decision = NO
```

### This Session

| Field | Value |
|-------|-------|
| Verdict | v1_better / v2_better / tie / both_inactive / both_missed / unusable |
| v1 score | /11 |
| v2 score | /11 |
| Counts toward 5-session decision | YES / NO |
| Key observation | (one sentence) |

---

## 6. ROLLUP INPUTS (exported to 5-session aggregation)

This session contributes the following to the final rollup:

```json
{
  "session_date": "YYYY-MM-DD",
  "classification": "...",
  "usable": true,
  "reversal_occurred": true,
  "v1": {
    "activated": true,
    "true_activation": true,
    "false_activation": false,
    "trades": 1,
    "expectancy": 50.0,
    "max_loss": 30.0,
    "tradable_rate": 0.65,
    "mfe_3x_rate": 0.40,
    "failure_modes": 0,
    "score": 5
  },
  "v2": {
    "activated": true,
    "true_activation": true,
    "false_activation": false,
    "trades": 1,
    "expectancy": 80.0,
    "max_loss": 25.0,
    "tradable_rate": 0.70,
    "mfe_3x_rate": 0.50,
    "failure_modes": 0,
    "compressions": 1,
    "revivals": 1,
    "score": 7
  },
  "verdict": "v2_better"
}
```

### 5-Session Decision Rule (from V2_VALIDATION_ADDENDUM)

After 5 usable sessions:

```
v2_wins = count(verdict == "v2_better")
v1_wins = count(verdict == "v1_better")
ties = count(verdict == "tie")

IF v2_wins >= 3:                    → REPLACE v1 with v2
ELIF v1_wins >= 3:                  → KEEP v1, reject v2
ELIF v2_wins >= 2 AND ties >= 2:    → REPLACE (v2 at least as good)
ELIF v1_wins >= 2 AND ties >= 2:    → KEEP v1
ELSE:                               → KEEP BOTH, route by market type
```

---

*Confidence in this template: 0.96*

*Two engineers using the same shadow logs and this template will produce the same verdict. The scoring is additive (+1/0/-1), the rules are if/elif, and the rollup inputs are a flat JSON.*
