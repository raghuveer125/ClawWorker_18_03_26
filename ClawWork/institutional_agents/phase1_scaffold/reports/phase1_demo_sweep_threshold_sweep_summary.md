# Threshold Sweep Recommendation (phase1_demo_sweep)

## Run Metadata
- Input file: sample_batch_input.csv
- Records: 6
- Total configs evaluated: 32
- Max veto % policy: 30.0

## Recommended Thresholds
- Bullish threshold: 0.30
- Bearish threshold: -0.30
- Strong-move threshold: 0.80
- Score: 2.3000
- Active decisions: 3
- Risk veto %: 33.33%

## Top Ranked Configurations
| Rank | Bullish | Bearish | Strong | Score | Active | Veto % |
|------|---------|---------|--------|-------|--------|--------|
| 1 | 0.30 | -0.30 | 0.80 | 2.3000 | 3 | 33.33% |
| 2 | 0.30 | -0.30 | 1.00 | 2.3000 | 3 | 33.33% |
| 3 | 0.40 | -0.30 | 0.80 | 2.2000 | 3 | 33.33% |
| 4 | 0.40 | -0.30 | 1.00 | 2.2000 | 3 | 33.33% |
| 5 | 0.30 | -0.40 | 0.80 | 1.3000 | 2 | 33.33% |

## Analyst Actions
- Validate the best config on a larger historical dataset.
- Compare selected config against current baseline in paper mode.
- Promote to shadow mode only after risk and reliability checks pass.

## Sign-off
- Product:
- Risk:
- Engineering: