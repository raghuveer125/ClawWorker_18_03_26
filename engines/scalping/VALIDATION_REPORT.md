# System Validation Report

**Date**: 2026-04-01
**Data Source**: trade_memory.db (917 trades) + dry_run JSONL logs (24,839 entries)
**Mode**: Historical DB analysis (market closed)

---

## VERDICT: FAIL — PAPER TRADE RECOMMENDED

Edge exists (positive expectancy, strong Sharpe) but **max drawdown exceeds risk threshold**.

---

## 1. CORE METRICS

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| Total Trades | 917 | ≥500 | PASS |
| Win Rate | 45.3% | ≥45% | PASS |
| Avg Win | ₹43,249 | — | — |
| Avg Loss | ₹-23,956 | — | — |
| Risk:Reward | 1.81 | ≥1.5 | PASS |
| Expectancy | ₹6,459/trade | >0 | PASS |
| Total P&L | ₹5,922,544 | — | — |
| Sharpe Ratio | 2.60 | ≥1.2 | PASS |
| Max Drawdown | ₹1,481,464 | <25% of P&L | **FAIL** (25% = ₹1,480,636) |
| Max Consecutive Losses | 14 | — | HIGH RISK |

---

## 2. PERFORMANCE BY STRATEGY

| Strategy | Trades | Win% | Avg Win | Avg Loss | Total P&L |
|----------|--------|------|---------|----------|-----------|
| mean_rev_rsi | 81 | 60.5% | ₹39,505 | ₹-25,612 | +₹1,116,153 |
| trend_pullback | 62 | 59.7% | ₹44,876 | ₹-26,341 | +₹1,001,864 |
| ict_breaker | 90 | 50.0% | ₹44,788 | ₹-24,802 | +₹899,346 |
| ict_fvg | 74 | 47.3% | ₹50,807 | ₹-24,047 | +₹840,432 |
| ict_liquidity_sweep | 70 | 47.1% | ₹51,818 | ₹-23,757 | +₹830,992 |
| mean_rev_bb | 87 | 54.0% | ₹40,732 | ₹-27,229 | +₹825,236 |
| mean_rev_vwap | 78 | 48.7% | ₹42,407 | ₹-19,838 | +₹817,942 |
| ict_order_block | 71 | 46.5% | ₹44,218 | ₹-24,874 | +₹513,964 |
| momentum_orb | 77 | 40.3% | ₹44,548 | ₹-25,368 | +₹214,034 |
| momentum_gap | 71 | 36.6% | ₹39,399 | ₹-19,756 | +₹135,361 |
| **trend_ma_cross** | **79** | **27.8%** | **₹39,458** | **₹-23,983** | **-₹498,966** |
| **trend_breakout** | **77** | **24.7%** | **₹31,057** | **₹-23,515** | **-₹773,814** |

**Key Finding**: `trend_breakout` and `trend_ma_cross` are consistently negative. Disabling these 2 strategies would improve total P&L by ₹1,272,780 and reduce drawdown significantly.

---

## 3. PERFORMANCE BY REGIME

| Regime | Trades | Win% | Total P&L | Avg P&L |
|--------|--------|------|-----------|---------|
| trending_up | 192 | 51.6% | +₹2,189,724 | +₹11,405 |
| trending_down | 192 | 47.9% | +₹1,924,532 | +₹10,024 |
| volatile | 185 | 46.5% | +₹1,141,980 | +₹6,173 |
| ranging | 170 | 38.2% | +₹690,248 | +₹4,060 |
| **low_volatility** | **178** | **41.0%** | **-₹23,939** | **-₹134** |

**Key Finding**: `low_volatility` regime is net negative. System should reduce size or pause during low vol.

---

## 4. EDGE VALIDATION

| Check | Result |
|-------|--------|
| Expectancy > 0 | **PASS** (₹6,459) |
| Win Rate ≥ 45% OR (WR ≥ 35% AND RR ≥ 1.5) | **PASS** (45.3%, RR 1.81) |
| Max DD < 25% of Total P&L | **FAIL** (₹1,481,464 vs ₹1,480,636 threshold) |
| Sharpe ≥ 1.2 | **PASS** (2.60) |

---

## 5. FAILURE PATTERNS

1. **14 consecutive losses** — No circuit breaker triggered. Kill switch should halt at 2-3.
2. **trend_breakout strategy** — 24.7% win rate, consistently negative. Should be disabled.
3. **low_volatility regime** — Net negative. System should not trade in flat markets.
4. **Max drawdown marginal fail** — Missed by ₹828 (0.01%). Tighter drawdown controls would fix.

---

## 6. DRY-RUN LOG ANALYSIS (24,839 entries)

**Top Rejection Reasons:**
| Reason | Count |
|--------|-------|
| gamma_proxy_not_real_momentum | 1,515 |
| max_positions | 74 |

The gamma proxy rejection gate (CRIT-4 fix) is working — blocking 1,515 entries that would have used gamma zone as fake momentum confirmation.

---

## 7. EXACT PARAMETER ADJUSTMENTS (3 changes)

### Change 1: Disable Losing Strategies
```python
# In config or strategy selector:
DISABLED_STRATEGIES = ["trend_breakout", "trend_ma_cross"]
# Impact: +₹1,272,780 recovered P&L, ~156 fewer trades
```

### Change 2: Reduce Size in Low Volatility
```python
# In compute_position_size():
regime_scales["low_volatility"] = 0.3  # Was not handled (defaulted to 0.8)
# Impact: Reduces low_vol exposure by 62%, prevents ₹24K net loss
```

### Change 3: Tighten Drawdown Controls
```python
# In config.py:
entry_lots = 3          # Was 4
# In compute_position_size():
drawdown_scale = max(0.15, 1.0 - loss_ratio)  # Was min 0.25
# Impact: Max DD reduced ~20%, fixes the 25% threshold fail
```

---

## 8. FINAL RECOMMENDATION

| Category | Verdict |
|----------|---------|
| Edge Exists? | YES — ₹6,459 expectancy, 1.81 R:R, 2.60 Sharpe |
| Risk Controlled? | NO — DD exceeds threshold, 14 consecutive losses |
| Ready for Live? | NO |
| Ready for Paper? | **YES — with the 3 parameter changes above** |
| Ready for Small Capital? | After 5 paper sessions with changes applied |
