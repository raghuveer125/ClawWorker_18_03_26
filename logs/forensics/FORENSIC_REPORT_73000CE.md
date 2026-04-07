# Forensic Report: SENSEX 73000 CE Missed Opportunity

**Date**: 2026-04-02
**Time**: 15:01 IST
**Investigator**: Quantitative Systems Auditor

---

## Market Timeline

| Time | SENSEX | Phase |
|------|--------|-------|
| 09:30 | ~73,300 (open) | Bearish open (-1.97%) |
| 10:00 | ~71,700 | Strong decline |
| 11:00 | 71,546 (low) | Bottom |
| 12:00 | ~72,500 | Recovery begins |
| 14:00 | ~73,000 | Bullish expansion |
| 15:00 | 73,187 | +2.3% from low |

## 73000 CE Behavior

- At market low (~11:00): premium ~₹2-5 (deep OTM, 1500+ pts away)
- At 14:00: premium expanding as spot approached 73000
- At 15:00: ~200 pts ITM, premium likely ₹15-25
- **Move**: ₹2-5 → ₹15-25 = 300-1150% expansion

## Failure Classification

| Rank | Cause | Description | Confidence |
|------|-------|-------------|------------|
| PRIMARY | A - Direction Failure | Persistent structure stuck at "bearish 0.98" despite 2.3% rally | 0.98 |
| SECONDARY | D - Strike Selection | 73000 CE too ITM for scalping config; 73300 CE selected instead | 0.90 |
| TERTIARY | F - No Signal | CE blocked = no CE signals generated | 1.00 |

## Root Cause

**Persistent structure bias has no decay mechanism.**

Evidence:
- Transient BOS: `bos_bullish` (detecting reversal correctly)
- Persistent bias: `Bearish bias, conf=0.98` (stale, contradicts BOS)
- Gate 8 Check 2 reads persistent bias → blocks CE
- Result: CE blocked for 4+ hours despite bullish market

## Counterfactual

| Scenario | Would trade execute? |
|----------|---------------------|
| Circuit breaker OFF | NO (not the blocker) |
| Persistent gate OFF | PARTIAL — 73300 CE selected, not 73000 |
| Persistent gate OFF + wider strike range | YES |

## Classification

**SYSTEM LIMITATION — not a bug.**

The persistent structure gate correctly prevented CE losses during the bearish phase (0% CE WR, -₹58 saved). The limitation is that it has no mechanism to FLIP when market reverses. The StructureAgent's summary text is write-once-per-trend, not dynamically updated.

## Confidence Score: 0.98
