# Expiry Opportunity Engine (EOE) — Design Specification

**Version**: 0.1 (Design only — not implemented)
**Type**: Low-frequency, high-asymmetry expiry capture system
**Status**: Blueprint pending 5-session shadow validation

---

## 1. OPPORTUNITY DEFINITION

### Setup Class: Expiry Reversal Momentum (ERM)

A cheap index option on weekly expiry day transitions from deep OTM toward ATM/ITM via intraday trend reversal, producing 10-50x premium expansion.

### Required Market Conditions (ALL must be present)

| Condition | Measurement | Threshold |
|-----------|-------------|-----------|
| **Expiry day** | Calendar check | Weekly expiry (Thu NSE, Fri BSE) |
| **Morning directional gap** | Open vs prev close | \|gap\| ≥ 0.5% |
| **Sustained morning trend** | Price moves in gap direction for 60+ min | LTP > 0.8% from open in gap direction |
| **Intraday reversal** | Price reverses against morning direction | ≥ 1.0% reversal from session extreme |
| **Structure confirmation** | ≥ 2 BOS events in reversal direction | Within 30-minute window |
| **Price crosses VWAP** | LTP crosses session VWAP in reversal direction | Must hold above/below for ≥ 5 min |
| **Premium availability** | Target option premium ₹2-15 | Bid-ask spread < 30% |

### What This Is NOT

- Not a gap-and-go momentum play (morning bias trade)
- Not a mean-reversion scalp (range-bound play)
- Not a straddle/strangle (volatility play)
- Not a lottery ticket buyer (random cheap options)

This is specifically: **a confirmed reversal captured through a cheap option that the reversal carries toward the money.**

---

## 2. ACTIVATION CONDITIONS

### EOE State Machine

```
OFF → WATCH → ARMED → ACTIVE → COOLDOWN → OFF
```

**OFF** (default): Not expiry day, or morning session (first 60 min). No action.

**WATCH** (activated automatically on expiry day after 10:30):
- Monitor for morning trend exhaustion
- Track session high/low and VWAP
- No trading

**ARMED** (transition when reversal detected):
- ≥ 1.5% move from session extreme against morning direction
- OR price crosses VWAP + holds 5 minutes
- Begin scanning for strikes
- No trading yet

**ACTIVE** (transition when structure confirms):
- ≥ 2 BOS events in reversal direction within 30 min
- Price made higher high (bullish) or lower low (bearish) vs last 30 min
- NOW eligible to enter
- Max duration: 90 minutes, then force COOLDOWN

**COOLDOWN** (after trade or timeout):
- No new entries for 30 min
- Monitor existing position only
- Return to OFF at 14:50 (late entry cutoff)

### Activation Timeline (using 2026-04-02 as reference)

| Time | State | Reason |
|------|-------|--------|
| 09:15-10:30 | OFF | Morning session, let trend establish |
| 10:30 | WATCH | Expiry monitoring begins |
| 11:30 | ARMED | Price reverses 1.5%+ from 71546 low |
| 12:30 | ACTIVE | 2+ bos_bullish events, price above VWAP |
| 14:00 | COOLDOWN | After trade or 90 min timeout |
| 14:50 | OFF | Late entry cutoff |

---

## 3. STRIKE SELECTION LOGIC

### Premium Range

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Min premium | ₹2 | Below this, bid-ask spread is >50%, untradable |
| Max premium | ₹15 | Above this, payoff multiple drops below 5x target |
| Sweet spot | ₹3-8 | Tradable, cheap enough for 10x+ potential |

### Distance from Spot

| Parameter | Value |
|-----------|-------|
| Min OTM distance | 100 pts (near ATM allowed) |
| Max OTM distance | 600 pts |
| ITM allowed | Up to 100 pts ITM (if premium still < ₹15) |

### Liquidity Filters

| Check | Threshold |
|-------|-----------|
| Bid-ask spread | ≤ 30% of premium |
| Bid quantity | ≥ 3x order size |
| Volume | ≥ 500 contracts |

### Selection Algorithm

```
1. List all strikes within distance range
2. Filter by premium range (₹2-15)
3. Filter by liquidity
4. Score by: premium * delta_sensitivity * liquidity_score
5. Select top 1 strike (not multiple)
```

### Key Difference from Scalping StrikeSelector

| Dimension | Scalping | EOE |
|-----------|----------|-----|
| Premium | ₹10-25 (or 4x on non-expiry) | ₹2-15 |
| OTM preference | Far OTM (150-800 pts) | Near ATM / slight OTM (100-600 pts) |
| Lot count | 1-4 lots | 1 lot only |
| Objective | ₹3-5 point scalp | 10-50x premium expansion |

---

## 4. ENTRY RULES

### Entry is NOT at first reversal signal. Entry is AFTER confirmation.

### Trigger Sequence

```
1. ARMED → ACTIVE transition confirmed (2+ BOS)
2. Wait for pullback (3-5 candles retracing 20-40% of reversal move)
3. Continuation candle (closes in reversal direction, beyond pullback)
4. Volume ≥ 1.5x average of last 10 candles
5. ENTER on next candle open
```

### Additional Filters (must pass)

| Filter | Rule |
|--------|------|
| Time | After 11:00, before 14:30 |
| Circuit breaker | EOE-specific CB: max 1 loss per session |
| Premium freshness | Quote < 5 seconds old |
| Spread check | Bid-ask ≤ 30% at entry |

### Entry Sizing

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max lots | 1 | Single lot only — this is a high-asymmetry bet |
| Max capital risk | 2% of total capital (₹2,000 on ₹100K) | Premium is the entire risk |
| Max premium spent | ₹15 × lot size | E.g., ₹150 for SENSEX (lot=10) |

---

## 5. EXIT RULES

### Goal: Let winners run, cut losers fast.

### Stop Loss

| Type | Rule |
|------|------|
| Hard SL | Premium drops 50% from entry | ₹5 entry → SL at ₹2.50 |
| Time SL | If position not profitable after 30 min → exit | Reversal not materializing |

### Target / Trail

| Level | Action |
|-------|--------|
| +3x entry | Book 30% of position |
| +5x entry | Book 30% of position, trail remaining at -20% |
| +10x entry | Book 20%, trail remaining at -15% |
| +20x entry | Book remaining OR trail at -10% |
| Session end (15:10) | Exit everything |

### Example: ₹5 Entry

| Premium reaches | Action | Qty (of 10) |
|----------------|--------|-------------|
| ₹15 (3x) | Sell 3 | 7 remaining |
| ₹25 (5x) | Sell 3, trail 4 at ₹20 | 4 remaining |
| ₹50 (10x) | Sell 2, trail 2 at ₹42.50 | 2 remaining |
| ₹100 (20x) | Exit OR trail at ₹90 | Final runner |
| 15:10 | Exit all remaining | 0 |

---

## 6. RISK MODEL

### Per-Trade Risk

| Metric | Value |
|--------|-------|
| Max loss | Premium × lot size (e.g., ₹5 × 10 = ₹50) |
| Max loss as % of capital | 0.05-0.15% (₹50-150 on ₹100K) |
| Expected win rate | 15-25% (most reversals don't follow through) |
| Expected payoff on win | 5-20x |
| Expected value | (0.20 × 10 × ₹50) - (0.80 × ₹50) = ₹100 - ₹40 = +₹60 |

### Session Risk

| Parameter | Limit |
|-----------|-------|
| Max trades per session | 2 |
| Max consecutive losses before EOE shutdown | 2 |
| Max daily capital at risk | 0.3% of total (₹300 on ₹100K) |

### Kill Conditions (EOE-specific)

| Condition | Action |
|-----------|--------|
| VIX > 30 | EOE OFF (too volatile, premiums already inflated) |
| Session range < 0.5% by 12:00 | EOE OFF (no reversal coming) |
| 2 consecutive EOE losses | EOE OFF for session |
| Underlying gap > 3% | EOE OFF (too extreme for reversal bet) |

---

## 7. INTEGRATION WITH EXISTING SYSTEM

### Architecture

```
┌─────────────────────────────────────┐
│         SCALPING ENGINE (existing)   │
│  21-agent pipeline                   │
│  risk_engine.py (14 gates)           │
│  Capital: ₹95,000 (95%)             │
└─────────────────────────────────────┘
        ↕ (shared data feeds only)
┌─────────────────────────────────────┐
│         EOE (new, separate)          │
│  Single-agent state machine          │
│  Own risk controls                   │
│  Capital: ₹5,000 (5%)               │
│  Max 1 lot per trade                 │
└─────────────────────────────────────┘
```

### Shared Resources (read-only)

| Resource | EOE Usage |
|----------|-----------|
| DataFeed agent output | Spot prices |
| OptionChain agent output | Premiums, spreads |
| Structure agent output | BOS events, persistent bias |
| MarketRegime agent output | Regime classification |

### Independent Resources (EOE-owned)

| Resource | Purpose |
|----------|---------|
| EOE state machine | WATCH/ARMED/ACTIVE/COOLDOWN |
| EOE position manager | Tracks EOE trades separately |
| EOE circuit breaker | Independent loss tracking |
| EOE trade log | Separate from scalping trades |

### Non-Interference Guarantees

1. EOE never modifies context.data (read-only consumer)
2. EOE has separate capital allocation (₹5,000 hard cap)
3. EOE trade IDs prefixed with `EOE_` (no collision with `POS_`)
4. EOE does not affect scalping entry/exit decisions
5. EOE can be enabled/disabled without restarting scalping engine

---

## 8. CONFIDENCE SCORE

| Dimension | Score | Reasoning |
|-----------|-------|-----------|
| Opportunity class validity | 0.80 | Pattern is real (verified in 2026-04-02 data) but N=1 |
| Activation logic soundness | 0.85 | Standard institutional reversal detection |
| Strike selection design | 0.75 | Untested on live data; spread/liquidity assumptions |
| Entry rules robustness | 0.70 | Pullback + confirmation is standard but untested |
| Exit rules | 0.80 | Scaled exit with trailing is well-established |
| Risk model | 0.90 | Max loss capped at premium; positive-skew math is sound |
| Integration safety | 0.95 | Fully separate module; cannot damage scalping |

**Overall Design Confidence: 0.82**

### What Would Raise Confidence to 0.95+

1. 5 expiry sessions of shadow testing (does ARMED/ACTIVE trigger correctly?)
2. Premium expansion data (do ₹3-8 options actually reach 10x?)
3. False activation rate (how often does reversal fail after 2 BOS?)
4. Liquidity reality check (can we actually buy ₹3 options with 30% spread?)

### What Would Lower Confidence

1. If 5 sessions show 0 valid activations → pattern too rare
2. If cheap options have 50%+ spreads consistently → untradable
3. If reversals after 2 BOS fail > 85% → expected value negative
4. If VIX regime changes make premiums structurally different

---

*This is a design specification only. Implementation requires shadow testing for 5 expiry sessions with measured (not estimated) data before any code is written.*
