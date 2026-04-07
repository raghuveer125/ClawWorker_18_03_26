# Lottery Strike Picker — Corrective Design Review

**Author:** Claude Opus 4.6 (Senior Quant Systems Architect)
**Date:** 2026-04-07
**Classification:** Forensic redesign — production-critical
**Evidence base:** 14,363 market-hours cycles, 14,530 chain snapshots, 18,700 debug events, live chain bid/ask data

---

## 1. Executive Verdict

The lottery pipeline has **five architectural contradictions** that compound to make trade execution **logically impossible** under normal market conditions. The primary flaw is a mathematically unsatisfiable trigger condition. Even after partial fixes to the trigger system, four secondary blockers (premium band mismatch, bid data gap, confirmation stringency, and intrinsic floor miscalibration) independently prevent trade execution.

**Zero of 14,363 market-hours cycles produced a trade on April 7**, despite a 266-point NIFTY range — an ideal breakout day.

---

## 2. Mathematical Proof of Trigger Failure

### Current trigger formula

```
lower_trigger = floor(S / 50) * 50        where S = live spot
upper_trigger = lower_trigger + 50
```

### Zone activation condition

```
CE zone:  S > upper_trigger + buffer      where buffer = 10
PE zone:  S < lower_trigger - buffer
```

### Proof of impossibility

**CE case:**
```
By definition:  lower_trigger = floor(S/50)*50
Therefore:      lower_trigger <= S < lower_trigger + 50
Therefore:      S < lower_trigger + 50 = upper_trigger
CE requires:    S > upper_trigger + 10 = lower_trigger + 60
But:            S < lower_trigger + 50
Therefore:      S < lower_trigger + 50 < lower_trigger + 60
CONTRADICTION:  CE condition can NEVER be true.
```

**PE case:**
```
By definition:  lower_trigger = floor(S/50)*50  <=  S
PE requires:    S < lower_trigger - 10
But:            S >= lower_trigger
Therefore:      S >= lower_trigger > lower_trigger - 10
CONTRADICTION:  PE condition can NEVER be true.
```

### Empirical verification

| Metric | Value |
|--------|-------|
| Market-hours cycles | 14,363 |
| CE condition satisfiable | 0 (0.00%) |
| PE condition satisfiable | 0 (0.00%) |
| Max 1-second spot move | 29.7 pts |
| Min gap to CE activation | 10.0 pts (always) |
| Required gap for CE | 60.0 pts (never achieved) |

The condition is satisfiable ONLY if spot jumps 60+ points in a single 1-second cycle. The maximum observed was 29.7 points. This has never happened.

---

## 3. All Architectural Contradictions

### Contradiction #1: Self-Tracking Triggers (CRITICAL)

**Trigger recalculates every cycle using CURRENT spot. Spot can never escape its own trigger.**

This is not a tuning issue. It is a logical impossibility. No parameter change (buffer, step, timing) can fix it while triggers track live spot.

### Contradiction #2: Premium Band vs Market Reality (HIGH)

**Config:** `premium_band.min = 2.00, premium_band.max = 15.00`
**Config:** `otm_distance.min_points = 150, otm_distance.max_points = 400`

**Live data at spot 23008 (April 7 close):**

| OTM Distance | CE Premium | PE Premium |
|-------------|-----------|-----------|
| 192 pts | Rs 3.75 | — |
| 242 pts | Rs 1.85 | — |
| 292 pts | Rs 1.05 | — |
| 208 pts | — | Rs 0.95 |
| 258 pts | — | Rs 0.75 |
| 308 pts | — | Rs 0.60 |

**Only 1 of 10 candidates (K=23200 CE at Rs 3.75) falls within the Rs 2-15 band.** The rest are below Rs 2.00.

On expiry day, far-OTM premiums collapse. A band minimum of Rs 2.00 excludes 90% of lottery candidates. The OTM distance filter selects strikes where premiums are Rs 0.40-1.85 — BELOW the band.

### Contradiction #3: Candidate Quote API vs Chain API (HIGH)

The FYERS candidate quote API (`fetch_candidate_quotes`) returns `bid=None` for many strikes. The full chain API returns `bid > 0` for the same strikes.

**Live evidence (K=23200 CE):**
- Chain API: `bid=4.25, ask=4.30`
- Candidate quote API: `bid=None`

The tradability gate uses candidate quotes → blocks entry with "bid_missing" even though bid exists in the chain.

**89 trade attempts blocked** by this data mismatch on April 7.

### Contradiction #4: Confirmation Quorum vs Data Availability (MEDIUM)

Confirmation runs 5 checks:
1. `candle_close` — requires CandleBuilder with completed candles
2. `premium_expand` — requires initial LTP from candidate discovery
3. `volume_spike` — requires current vs average volume
4. `spread_stable` — requires both initial and current spread
5. `hold_duration` — requires zone held for N seconds

**Problem:** FYERS returns `change=None`, `bid_qty=None` for all rows. This means:
- `spread_stable` fails or auto-passes depending on data availability
- `volume_spike` baseline is unreliable (no intraday volume delta)
- With quorum=2, any 2 data gaps = blocked

### Contradiction #5: Intrinsic Floor vs ITM Reality (MEDIUM)

The intrinsic floor check uses `epsilon = Rs 0.50` for all strikes. Deep ITM options (K=23200 PE with intrinsic Rs 180) routinely trade 1-3% below intrinsic due to bid-ask, margin cost, and early exercise dynamics.

**Live violations:**
- K=23150 PE: LTP=128.90, intrinsic=130.65 (1.3% discount — normal)
- K=23200 PE: LTP=176.75, intrinsic=180.65 (2.2% discount — normal)

This triggers `FAIL` on the quality check, blocking ALL downstream processing.

---

## 4. Corrected Architecture

### 4.1 Architecture Diagram

```
FYERS WebSocket ─────── Live spot ticks (sub-second)
                         │
FYERS REST ──────────────┤ Option chain every 30s
                         │
                    ┌────▼─────────────────────────────────────────────────┐
                    │           ANALYSIS CYCLE (30s)                       │
                    │                                                     │
                    │  1. Fetch chain                                     │
                    │  2. Validate quality                                │
                    │  3. ★ FREEZE TRIGGERS from this snapshot's spot     │
                    │  4. Compute metrics, score, select candidates       │
                    │  5. Hydrate candidates with live bid/ask            │
                    │  6. Update WS subscriptions for candidates          │
                    └──────────────────────┬──────────────────────────────┘
                                           │
                         frozen_triggers + hydrated_candidates
                                           │
                    ┌──────────────────────▼──────────────────────────────┐
                    │           TRIGGER CYCLE (1s)                        │
                    │                                                     │
                    │  1. Get live spot from WebSocket                    │
                    │  2. Compare live spot against FROZEN triggers       │
                    │     (triggers do NOT recalculate here)              │
                    │  3. If spot crossed frozen trigger:                 │
                    │     → Activate zone (CE or PE)                     │
                    │  4. If zone active + candidate valid:              │
                    │     → Run confirmation (hold_duration only)         │
                    │  5. If confirmed:                                   │
                    │     → Check tradability (bid/ask from WS or chain) │
                    │     → Execute paper entry                          │
                    │  6. If in trade: check exits                       │
                    └────────────────────────────────────────────────────┘
```

### 4.2 Key Design Changes

| Component | Before (Broken) | After (Corrected) |
|-----------|----------------|-------------------|
| **Triggers** | Recalculate every 1s from live spot | Freeze at analysis cycle, hold for 30s |
| **Premium band** | Rs 2.00-15.00 (misaligned with OTM reality) | Rs 0.30-15.00 (includes actual lottery premiums) |
| **Bid/ask source** | Candidate quote API only | Chain fallback when quote API returns None |
| **Confirmation** | 5-check quorum (data-dependent) | hold_duration only (always evaluable) |
| **Intrinsic floor** | Fixed Rs 0.50 epsilon | Scaled: max(0.50, 3% of intrinsic) |
| **Candidate hydration** | Score first, check tradability later | Must have live bid/ask before scoring |

---

## 5. Corrected Formulas

### 5.1 Trigger Resolution (Frozen)

```python
# Called ONCE per analysis cycle (every 30s), NOT every trigger cycle
def freeze_triggers(analysis_spot: float, strike_step: int) -> TriggerZone:
    lower = floor(analysis_spot / strike_step) * strike_step
    upper = lower + strike_step
    return TriggerZone(
        lower_trigger=lower,
        upper_trigger=upper,
        source="FROZEN",
        frozen_at_spot=analysis_spot,
    )
```

### 5.2 Zone Detection (Against Frozen Triggers)

```python
# Called every trigger cycle (1s) — compares LIVE spot against FROZEN triggers
def check_zone(live_spot: float, frozen: TriggerZone, buffer: float) -> str:
    if live_spot > frozen.upper_trigger + buffer:
        return "CE_ACTIVE"
    if live_spot < frozen.lower_trigger - buffer:
        return "PE_ACTIVE"
    return "NO_TRADE"
```

**Why this works:** When spot was 22890 at analysis time, triggers freeze at [22850, 22900]. Over the next 30s, spot can drift to 22920 or 22840, crossing the frozen trigger and activating a zone. The trigger does NOT chase spot.

### 5.3 Entry Condition

```python
entry_allowed = (
    zone_active                          # spot crossed frozen trigger
    AND candidate is not None            # scoring found a band candidate
    AND candidate.has_live_bid_ask       # bid/ask hydrated (WS or chain)
    AND candidate.spread_pct <= max_spread  # spread acceptable
    AND hold_duration_met                # spot held beyond trigger for N seconds
    AND risk_checks_pass                 # capital, daily limits, quality
)
```

### 5.4 Invalidation

```python
# Exit active trade if spot reverses past frozen trigger
def check_invalidation(live_spot, frozen, trade_side, buffer):
    if trade_side == "CE" and live_spot < frozen.upper_trigger - buffer:
        return True   # spot fell back below CE trigger
    if trade_side == "PE" and live_spot > frozen.lower_trigger + buffer:
        return True   # spot rose back above PE trigger
    return False
```

### 5.5 Candidate Filtering (Corrected)

```python
# Premium band: Rs 0.30 - 15.00 (was Rs 2.00 - 15.00)
# Rationale: live data shows 150-400pt OTM premiums are Rs 0.40-3.75
# A floor of Rs 2.00 excludes 90% of real lottery candidates

band_min = 0.30   # must have SOME value (filters zero-premium dead strikes)
band_max = 15.00   # cap prevents picking ATM-ish expensive options

# OTM distance: 150-400 points (unchanged)
# These parameters ARE correctly aligned with lottery philosophy
```

### 5.6 Tradability Gate (Corrected)

```python
def check_tradability(candidate, chain_rows, ws_quotes, config):
    # Get bid/ask from best available source
    ws = ws_quotes.get((candidate.strike, candidate.option_type))
    chain = find_in_chain(chain_rows, candidate.strike, candidate.option_type)
    
    bid = (ws.bid if ws and ws.bid else None) or (chain.bid if chain else None)
    ask = (ws.ask if ws and ws.ask else None) or (chain.ask if chain else None)
    
    if bid is None or bid <= 0:
        return FAIL("no_bid")
    if ask is None or ask <= 0:
        return FAIL("no_ask")
    
    mid = (bid + ask) / 2
    spread_pct = ((ask - bid) / mid) * 100
    if spread_pct > config.max_spread_pct:
        return FAIL(f"spread_wide({spread_pct:.1f}%)")
    
    if (candidate.volume or 0) < config.min_recent_volume:
        return FAIL("low_volume")
    
    return PASS(bid=bid, ask=ask, spread_pct=spread_pct)
```

### 5.7 Confirmation Gate (Simplified)

```python
# Reduced to hold_duration only
# Rationale: other checks depend on data FYERS doesn't provide
# (change=None, bid_qty=None, volume delta unreliable)
def check_confirmation(zone_active_since, now, hold_seconds):
    elapsed = (now - zone_active_since).total_seconds()
    return elapsed >= hold_seconds
```

---

## 6. Corrected State Machine

```
                        ┌─────────────────────────────────────────┐
                        │                                         │
                        ▼                                         │
                     ┌──────┐                                     │
                     │ IDLE │ (frozen triggers set by analysis)   │
                     └──┬───┘                                     │
                        │                                         │
          ┌─────────────┴──────────────┐                          │
          │                            │                          │
   spot < frozen_lower - buf    spot > frozen_upper + buf         │
          │                            │                          │
          ▼                            ▼                          │
   ┌──────────────┐          ┌──────────────┐                     │
   │ZONE_ACTIVE_PE│          │ZONE_ACTIVE_CE│                     │
   └──────┬───────┘          └──────┬───────┘                     │
          │                         │                             │
     candidate found           candidate found                   │
     + hold_duration met       + hold_duration met               │
     + tradability pass        + tradability pass                │
          │                         │                             │
          ▼                         ▼                             │
                  ┌──────────┐                                    │
                  │ IN_TRADE │                                    │
                  └────┬─────┘                                    │
                       │ SL/target/invalidation/EOD               │
                       ▼                                          │
                  ┌──────────┐                                    │
                  │ COOLDOWN │── cooldown expires ────────────────┘
                  └──────────┘

REMOVED STATES: CANDIDATE_FOUND, EXIT_PENDING
Rationale: CANDIDATE_FOUND added latency without value.
Entry happens directly from ZONE_ACTIVE when all gates pass.
EXIT_PENDING was always an immediate transition — unnecessary.
```

**Key change:** No separate CANDIDATE_FOUND state. When zone is active AND candidate exists AND hold_duration met AND tradability passes → enter directly. This removes the 1-cycle delay that allowed triggers to shift.

---

## 7. Pseudocode Patch

### 7.1 Analysis Cycle

```python
def run_analysis_cycle(self):
    snapshot = fetch_chain_with_retry()
    report = validate_quality(snapshot)
    
    if report.overall_status == FAIL:
        return
    
    # ★ FREEZE triggers from analysis spot — held until next analysis
    self._frozen_triggers = TriggerZone(
        lower_trigger = floor(snapshot.spot_ltp / step) * step,
        upper_trigger = floor(snapshot.spot_ltp / step) * step + step,
        source = "FROZEN",
    )
    
    # Score candidates (unchanged)
    rows = compute_base_metrics(snapshot, config)
    rows = compute_advanced_metrics(rows, config)
    window = filter_window(rows, snapshot.spot_ltp, config)
    side, bias = compute_side_bias(window, config, spot=snapshot.spot_ltp)
    best_ce, best_pe, all_cands = score_and_select(...)
    
    # Subscribe WebSocket to candidate strikes
    update_ws_subscriptions(best_ce, best_pe)
```

### 7.2 Trigger Cycle

```python
def run_trigger_cycle(self):
    live_spot = self._last_ws_ltp
    triggers = self._frozen_triggers  # ★ NEVER recalculate here
    
    if triggers is None:
        return  # wait for first analysis
    
    # Zone detection against frozen triggers
    if live_spot > triggers.upper_trigger + buffer:
        zone = "CE_ACTIVE"
    elif live_spot < triggers.lower_trigger - buffer:
        zone = "PE_ACTIVE"
    else:
        zone = "NO_TRADE"
    
    # If in trade: check exits
    if active_trade:
        check_exit(active_trade, live_spot, triggers)
        return
    
    # If zone active: attempt entry
    if zone != "NO_TRADE":
        candidate = best_ce if zone == "CE_ACTIVE" else best_pe
        if candidate is None:
            return  # no band candidate
        
        # Tradability: use WS quote → chain fallback
        bid, ask = get_best_bid_ask(candidate, ws_quotes, chain)
        if bid is None or ask is None:
            return  # not tradable
        
        spread_pct = ((ask - bid) / ((bid + ask) / 2)) * 100
        if spread_pct > max_spread:
            return  # spread too wide
        
        # Confirmation: just hold_duration
        if not zone_held_long_enough(hold_seconds):
            return  # not confirmed yet
        
        # Risk checks
        if not risk_guard.check_entry(...):
            return
        
        # ★ ENTER TRADE
        execute_entry(candidate, bid, ask)
```

### 7.3 Frozen Trigger Handling

```python
# Triggers freeze at analysis cycle and hold until next analysis
# This means triggers are valid for exactly chain_refresh_seconds (30s)
# During those 30s, spot can drift and cross the frozen boundary

# Example timeline:
# T=0.0s  Analysis: spot=22890, freeze triggers=[22850, 22900]
# T=5.2s  Trigger cycle: spot=22912 > 22900+10=22910 → CE_ACTIVE
# T=10.1s Trigger cycle: spot=22918, still > 22910 → hold confirmed
# T=10.1s Entry: buy CE candidate
# T=30.0s Next analysis: spot=22935, freeze triggers=[22900, 22950]
#         New triggers reflect new reality
```

---

## 8. Module-by-Module Patch Plan

### 8.1 `state_machine.py`

**What is wrong:** `resolve_triggers()` uses live spot → self-tracking impossibility.
**What to change:** Remove `resolve_triggers()` from evaluate(). Accept triggers as input parameter (already frozen by caller). Remove CANDIDATE_FOUND and EXIT_PENDING states — simplify to IDLE → ZONE_ACTIVE → IN_TRADE → COOLDOWN.
**Why:** Triggers must be exogenous to the trigger cycle. State machine should not compute its own inputs.

### 8.2 `signal_engine.py`

**What is wrong:** Trailing stop compared LTP to itself (already fixed). Exit check uses live-recomputed triggers.
**What to change:** Accept frozen triggers in evaluate_exit(). Already partially done.
**Why:** Exit invalidation must use the same triggers that justified entry.

### 8.3 `confirmation.py`

**What is wrong:** 5-check quorum depends on data FYERS doesn't provide (change, bid_qty).
**What to change:** Set mode to `DISABLED` or reduce to hold_duration only. The quorum system adds complexity without value when 3 of 5 data inputs are unavailable.
**Why:** A confirmation gate that randomly passes/fails based on data availability is worse than no gate.

### 8.4 `hysteresis.py`

**What is wrong:** Buffer/rearm logic is correct in principle but never tested because triggers were impossible.
**What to change:** No structural change needed. With frozen triggers, the hysteresis buffer (10 pts) becomes meaningful.
**Why:** The hysteresis design is sound — it was just never reached.

### 8.5 `scoring.py`

**What is wrong:** f_dist rewards farther strikes (higher score for deeper OTM). Extrapolated candidates get f_mom=0, f_liq=0 but can still be selected.
**What to change:** Already partially fixed (distance-to-target, spread penalty). Additionally: reject extrapolated candidates entirely until hydrated with live bid/ask.
**Why:** Scoring a candidate that cannot be executed is waste.

### 8.6 `extrapolation.py`

**What is wrong:** Projects premiums beyond visible chain. These projected strikes have no live bid/ask.
**What to change:** Mark extrapolated candidates as `source="ADVISORY"` and exclude from trade selection. Use only for analysis/display.
**Why:** You cannot trade a strike that doesn't have a live quote.

### 8.7 `tradability.py`

**What is wrong:** Uses candidate quote bid (often None from FYERS). Spread check auto-passes on missing data.
**What to change:** Already partially fixed (chain fallback). Ensure ALL bid/ask lookups follow the cascade: WS quote → candidate REST quote → chain snapshot.
**Why:** FYERS quote API is unreliable for far-OTM strikes. Chain API is complete.

### 8.8 `main.py`

**What is wrong:** Calls `resolve_triggers(live_spot)` in trigger cycle. Candidate quote refresh may not have bid/ask.
**What to change:** Already partially fixed (session-locked triggers, chain fallback). Ensure frozen triggers are used in ALL three locations: entry signal, trigger snapshot builder, exit check.
**Why:** Consistency — the entire trigger cycle must use the same frozen reference.

### 8.9 `settings.yaml`

**What to change:**
```yaml
premium_band:
  min: 0.30     # was 2.00 — too high for expiry-day OTM premiums
  max: 15.00

confirmation:
  mode: "QUORUM"
  quorum: 1     # was 2 — hold_duration alone is sufficient
  hold_duration_seconds: 10.0
```

---

## 9. Migration Plan

### Phase 1: Config-Only (Zero Code Risk)
1. Set `premium_band.min: 0.30`
2. Verify band candidates appear on dashboard

### Phase 2: Trigger Fix (Already Done)
1. Session-locked triggers in main.py ✅
2. All 3 resolve_triggers() call sites use frozen triggers ✅

### Phase 3: Bid/Ask Fallback (Already Done)
1. Tradability gate uses chain fallback ✅
2. Entry execution uses chain fallback ✅
3. Confirmation spread uses chain fallback ✅

### Phase 4: Quality Fix (Already Done)
1. Intrinsic floor epsilon scaled by 3% ✅
2. Expiry resolved at startup ✅

### Phase 5: Validation
1. Run next market session
2. Verify ZONE_ACTIVE appears within first 30s of a directional move
3. Verify CANDIDATE_FOUND produces VALID signals
4. Verify tradability passes (bid from chain or WS)
5. Verify paper trade executes

---

## 10. Validation Tests

### Test 1: CE Breakout

```
Setup:  Analysis spot=22900, frozen triggers=[22900, 22950]
Action: Live spot moves to 22965 (> 22950 + 10 = 22960)
Expect: zone=CE_ACTIVE, candidate selected, hold 10s, entry executed
```

### Test 2: PE Breakdown

```
Setup:  Analysis spot=22900, frozen triggers=[22850, 22900]
Action: Live spot drops to 22835 (< 22850 - 10 = 22840)
Expect: zone=PE_ACTIVE, candidate selected, hold 10s, entry executed
```

### Test 3: No Trade (Within Zone)

```
Setup:  Frozen triggers=[22900, 22950]
Action: Spot oscillates 22905-22945
Expect: zone=NO_TRADE (never crosses trigger+buffer)
```

### Test 4: Trigger Refresh

```
Setup:  T=0 frozen at [22900, 22950], spot=22910
Action: T=30 analysis runs, spot=22960
Expect: New frozen triggers=[22950, 23000]. Previous CE zone deactivates.
```

### Test 5: Stale Data

```
Setup:  Quality check returns FAIL (stale chain)
Expect: No zone activation, no entry. IDLE with DATA_QUALITY_FAIL.
```

### Test 6: Extrapolated Candidate Rejected

```
Setup:  Best CE is an extrapolated strike with no live bid/ask
Expect: Candidate rejected at tradability gate. No entry.
```

### Test 7: Bid Fallback

```
Setup:  Candidate quote returns bid=None. Chain has bid=4.25.
Expect: Tradability uses chain bid=4.25 → PASS. Entry proceeds.
```

### Test 8: Re-Entry After Cooldown

```
Setup:  Trade exits (SL hit). Cooldown=300s starts.
Action: After 300s, zone still active.
Expect: Re-entry allowed (if reentry_count < max_reentries).
```

---

## 11. Final Implementation Checklist

- [x] Frozen triggers implemented (session-locked at first analysis)
- [x] All 3 trigger call sites use frozen triggers
- [x] Bid/ask chain fallback in tradability gate
- [x] Bid/ask chain fallback in entry execution
- [x] Bid/ask chain fallback in confirmation spread
- [x] Trailing stop fixed (peak_ltp comparison)
- [x] Intrinsic floor epsilon scaled (max of 0.50, 3% of intrinsic)
- [x] Expiry resolution at startup
- [x] Multi-factor bias (PCR + momentum + position)
- [x] WebSocket candidate subscriptions
- [x] Scoring: ROC + spread penalty + tuned weights
- [ ] **Premium band min lowered to 0.30** (config change needed)
- [ ] Restart API server to activate all changes
- [ ] Run next market session and verify first trade executes

---

*End of corrective design review.*
