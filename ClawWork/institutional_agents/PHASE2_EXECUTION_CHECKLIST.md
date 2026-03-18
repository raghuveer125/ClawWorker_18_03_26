# Phase 2 Execution Checklist (Options Engine)

## 1) Setup
- [ ] Confirm Phase 1 closure artifacts available
- [ ] Lock Phase 2 thresholds and scoring weights
- [ ] Prepare sample option-chain and greek-enriched datasets

## 2) Build
- [ ] Implement options contracts and parser
- [ ] Implement options analyst scoring
- [ ] Implement decision-layer merge with Phase 1 momentum signal
- [ ] Implement risk veto propagation

## 3) Validate
- [ ] Run Phase 2 test matrix
- [ ] Validate schema for all output payloads
- [ ] Validate guardrails for spread/OI/volume/data completeness

## 4) Simulate
- [ ] Run paper mode with options-enhanced logic
- [ ] Export report and compare against Phase 1 baseline
- [ ] Run Phase 2 batch pipeline and generate markdown report
- [ ] Run baseline comparison utility (`phase2_scaffold/phase2_baseline_comparison.py`) with outcomes files

## 5) Exit Gate
- [ ] False-signal reduction demonstrated
- [ ] Risk metrics not degraded
- [ ] Phase 2 summary report published
- [ ] Release gate script passes (`phase2_release_check.py`)
- [ ] Quality-gate script passes (`phase2_quality_check.py`)
- [ ] Sign-off note generated (`phase2_signoff_generator.py`)
