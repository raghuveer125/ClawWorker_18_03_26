================================================================================
SCALPING ENGINE ENTRY CRITERIA - QUICK REFERENCE
================================================================================

MINIMUM 2 OF 4 ENTRY CONDITIONS (REQUIRED)
──────────────────────────────────────────
1. Structure Breakout          → config.require_structure_break
2. Futures Momentum (0.7+)     → config.require_futures_confirm
3. Volume Burst (5x avg)       → config.require_volume_burst
4. Trap Confirmation (opt)     → config.require_trap_confirm = False

WITHOUT 2 CONDITIONS: INSTANT REJECTION


SETUP QUALITY TAGS & SIZE MULTIPLIERS
──────────────────────────────────────

A+ SETUP (FULL ALIGNMENT):
  Requires ALL:
    • rr_ratio >= 1.3
    • 5m trend aligned
    • 3m breakout aligned
    • 1m momentum aligned
    • Entry trigger active (vol burst OR liquidity vacuum)
  Base multiplier: 0.65 → up to 0.70
  Entry can proceed with high conviction

B SETUP (GOOD ALIGNMENT):
  Requires ALL:
    • rr_ratio >= 1.1
    • 3m breakout aligned
    • 1m momentum aligned
  Base multiplier: 0.35 → up to 0.40
  Entry can proceed normally

C SETUP (WEAK ALIGNMENT):
  When B not met but:
    • rr_ratio >= 1.1
  Base multiplier: 0.25
  BLOCKS if: rr_ratio < 1.1 OR strict_a_plus_only = True

F SETUP (NO ALIGNMENT):
  Base multiplier: 0.0
  ALWAYS REJECTED


R:R RATIO - THE FINAL GATE
───────────────────────────
rr = reward / risk
where:
  risk = entry - SL (SL = entry × 0.75)
  reward = target - entry

Rejection:
  • A+: rr < 1.3 blocks trade
  • B:  rr < 1.1 blocks trade
  • C:  rr < 1.1 blocks trade (else 0% multiplier)


QUALITY SCORE FILTER
────────────────────
Grade A+: >= 0.90 → +2 priority
Grade A:  >= 0.80 → normal priority
Grade B:  >= 0.70 → normal priority
Grade C:  >= 0.60 → -2 priority
Grade D:  >= 0.50 → skip
Grade F:  < 0.50  → reject

Pass requires ALL:
  1. total_score >= 0.5
  2. confidence >= 0.5
  3. regime_score >= 0.3


POSITION MULTIPLIER CALCULATION
────────────────────────────────
base = determined_by_tag(tag, rr_ratio)
base *= vix_adjustment(vix) [>25: 0.7x | <15: 1.0x]
base *= spread_adjustment(spread_pct) [>0.5%: 0.5x]
base *= dealer_adjustment(gamma_regime, pinning)
final_multiplier = clamp(0.0, 1.0, base)

Rejection if: multiplier < 0.2


FINAL ORDER SIZE
────────────────
order_lots = max(1, round(entry_lots × effective_multiplier × queue_size_scale))

Where:
  entry_lots = 4 (from config)
  effective_multiplier = base × correlation_scale × vacuum_boost
  queue_size_scale = 0.0 → 1.0 (0 = rejected)


PRE-ENTRY GATES (ALL MUST PASS)
────────────────────────────────
1. trade_disabled == False
2. current_time <= 14:50 (late_entry_cutoff_time)
3. open_positions < 3 (max_positions)
4. quality_filtered_signals not empty


FILL CONDITION CHECKS
─────────────────────
Slippage:     ((ask - premium) / premium) × 100 <= 2.0%
Bid/Ask Drift: ((ask - reference) / reference) × 100 <= 1.0%
Spread Widen:  current_spread / reference_spread <= 1.5x

Any failure: Reject trade


LIQUIDITY REQUIREMENTS (ALL MUST PASS)
──────────────────────────────────────
1. spread_pct <= 5.0%
2. bid_depth >= 100
3. ask_depth >= 100
4. (volume >= 500 OR oi >= 5000)

Any failure: Reject strike


STRIKE SELECTION CRITERIA
──────────────────────────
Direction: Based on structure.trend + futures_surge.direction

OTM Distance:
  NIFTY50:   150-300 points (VIX adjusted)
  BANKNIFTY: 300-600 points
  SENSEX:    400-800 points

Premium:     ₹10-25 (₹12-18 optimal)
Delta:       0.15-0.25 (0.18-0.22 optimal)
Spread:      < 5% (< 2% ideal)

Top 5 ranked strikes selected


MOMENTUM SIGNAL STRENGTH THRESHOLDS
────────────────────────────────────
Entry requires strength >= 0.7 for:
  • Futures Surge: |move| >= threshold (NIFTY50: 25pts)
  • Volume Spike: current >= 5x average (last 20)
  • Gamma Expansion: premium up >= 15%
  • Gamma Zone: 4+ options delta 0.40-0.60

High momentum: >= 0.8


VIX IMPACT ON STRIKES
──────────────────────
VIX >= 25:  OTM range × 0.80 (move closer to ATM)
            position size × 0.70 (reduce)
VIX <= 15:  OTM range × 1.15 (move further OTM)
            position size × 1.0 (maintain)


DEALER PRESSURE IMPACT
──────────────────────
Gamma Regime: "short" | "long" | "neutral"
Pinning Score: 0-1 (extreme threshold: >= 0.85)

If short gamma AND acceleration >= 0.6:
  +0.08 momentum score

If long gamma AND pinning >= 0.85:
  -0.08 confidence score
  -10% position size


REGIME COMPATIBILITY (Signal Quality)
──────────────────────────────────────
TRENDING_BULLISH: CE=1.0, PE=0.3
TRENDING_BEARISH: CE=0.3, PE=1.0
RANGE_BOUND:      CE=0.6, PE=0.6
VOLATILE_EXPAND:  CE=0.8, PE=0.8
VOLATILE_CONTR:   CE=0.4, PE=0.4
EXPIRY_PINNING:   CE=0.5, PE=0.5

If regime_score < 0.3: Signal rejected


REJECTIONS BY STAGE
────────────────────
1. Strike Selection → "No viable strikes"
2. Quality Filter → Grade F/D/C issues
3. Liquidity → Spread/depth/volume fail
4. Entry Gate → Trade disabled/cutoff/max positions
5. Conditions → < 2 of 4 met
6. Setup → Tag mismatch/R:R fail/timeframe gaps
7. Multiplier → < 0.2
8. Fill → Slippage/drift/spread issues
9. Queue → size_scale <= 0.0


CRITICAL THRESHOLDS (From Config)
───────────────────────────────────
Entry Lots:                  4
Max Positions:               3
Max Entry Slippage:          2.0%
Max Bid/Ask Drift:           1.0%
Max Spread Widening:         1.5x
Late Entry Cutoff:           14:50
Execution Loop:              300ms
Min Volume:                  1000
Min OI:                      5000
Max Spread %:                5.0%
Option Expansion Trigger:    15%
High VIX Level:              25.0
Low VIX Level:               15.0
Realized Vol Threshold:      0.012
OI Buildup Threshold:        50%
PCR Spike Threshold:         0.3


QUICK DECISION TREE
────────────────────

Signal generated?
  → No: No entry
  
Quality check (A+/A/B/C)?
  → F/D: No entry
  
Liquidity ok?
  → No: No entry

Trade enabled + time ok + slots available?
  → No: No entry

2+ of 4 conditions met?
  → No: No entry

Setup tag A+/B/C assigned?
  → C with bad R:R: No entry

R:R ratio ok for tag?
  → No: No entry

Multiplier >= 0.2?
  → No: No entry

Fill conditions ok?
  → No: No entry

Queue risk safe?
  → No: No entry

→ EXECUTE ORDER

================================================================================
