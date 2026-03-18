# Phase 1 Paper Backtest Report (phase1_20day_pipeline)

## Run Metadata
- Input file: reports/phase1_20day_realdata_input.csv
- Record count: 102

## Action Summary
- BUY_CALL: 26 (25.49%)
- BUY_PUT: 26 (25.49%)
- NO_TRADE: 50 (49.02%)

## Confidence Summary
- HIGH: 25
- MEDIUM: 27
- LOW: 50

## Risk Guard Summary
- Risk veto count: 0
- Risk veto %: 0.0%

## Decision Sample
| # | Underlying | Action | Confidence | Strike | Rationale |
|---|------------|--------|------------|--------|-----------|
| 1 | NIFTY50 | NO_TRADE | LOW | None | No clear momentum edge (-0.10%). |
| 2 | BANKNIFTY | NO_TRADE | LOW | None | No clear momentum edge (0.06%). |
| 3 | SENSEX | NO_TRADE | LOW | None | No clear momentum edge (-0.08%). |
| 4 | NIFTY50 | BUY_CALL | MEDIUM | 26400 | Bullish momentum detected (0.70%). |
| 5 | BANKNIFTY | BUY_CALL | MEDIUM | 60300 | Bullish momentum detected (0.74%). |
| 6 | SENSEX | BUY_CALL | MEDIUM | 85900 | Bullish momentum detected (0.67%). |
| 7 | NIFTY50 | NO_TRADE | LOW | None | No clear momentum edge (-0.30%). |
| 8 | BANKNIFTY | NO_TRADE | LOW | None | No clear momentum edge (-0.18%). |
| 9 | SENSEX | NO_TRADE | LOW | None | No clear momentum edge (-0.38%). |
| 10 | NIFTY50 | NO_TRADE | LOW | None | No clear momentum edge (-0.27%). |
| 11 | BANKNIFTY | NO_TRADE | LOW | None | No clear momentum edge (0.12%). |
| 12 | SENSEX | BUY_PUT | MEDIUM | 85000 | Bearish momentum detected (-0.44%). |
| 13 | NIFTY50 | NO_TRADE | LOW | None | No clear momentum edge (-0.14%). |
| 14 | BANKNIFTY | NO_TRADE | LOW | None | No clear momentum edge (-0.21%). |
| 15 | SENSEX | NO_TRADE | LOW | None | No clear momentum edge (-0.12%). |
| 16 | NIFTY50 | BUY_PUT | HIGH | 25850 | Bearish momentum detected (-1.01%). |
| 17 | BANKNIFTY | BUY_PUT | MEDIUM | 59600 | Bearish momentum detected (-0.51%). |
| 18 | SENSEX | BUY_PUT | HIGH | 84100 | Bearish momentum detected (-0.92%). |
| 19 | NIFTY50 | BUY_PUT | MEDIUM | 25650 | Bearish momentum detected (-0.75%). |
| 20 | BANKNIFTY | BUY_PUT | MEDIUM | 59200 | Bearish momentum detected (-0.73%). |

_Showing first 20 of 102 decisions._

## Analyst Notes
- What worked:
- What failed/edge cases:
- Recommended threshold updates:

## Sign-off
- Product:
- Risk:
- Engineering: