# Phase 1 Paper Backtest Report (phase1_demo_pipeline)

## Run Metadata
- Input file: sample_batch_input.csv
- Record count: 6

## Action Summary
- BUY_CALL: 2 (33.33%)
- BUY_PUT: 0 (0.0%)
- NO_TRADE: 4 (66.67%)

## Confidence Summary
- HIGH: 0
- MEDIUM: 2
- LOW: 4

## Risk Guard Summary
- Risk veto count: 2
- Risk veto %: 33.33%

## Decision Sample
| # | Underlying | Action | Confidence | Strike | Rationale |
|---|------------|--------|------------|--------|-----------|
| 1 | NIFTY50 | BUY_CALL | MEDIUM | 22200 | Bullish momentum detected (0.51%). |
| 2 | NIFTY50 | NO_TRADE | LOW | None | No clear momentum edge (-0.32%). |
| 3 | BANKNIFTY | NO_TRADE | LOW | None | No clear momentum edge (-0.28%). |
| 4 | BANKNIFTY | NO_TRADE | LOW | None | Risk veto triggered. |
| 5 | SENSEX | BUY_CALL | MEDIUM | 73000 | Bullish momentum detected (0.45%). |
| 6 | SENSEX | NO_TRADE | LOW | None | Risk veto triggered. |

## Analyst Notes
- What worked:
- What failed/edge cases:
- Recommended threshold updates:

## Sign-off
- Product:
- Risk:
- Engineering: