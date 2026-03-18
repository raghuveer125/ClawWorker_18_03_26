# Phase 2 Test Cases (Options Engine)

## A) Data Contract
- [ ] A1: Option chain row missing strike/type -> invalid row handling
- [ ] A2: Missing Greeks fields -> confidence downgrade or veto
- [ ] A3: Missing IV context -> fallback behavior validated

## B) Liquidity / Spread
- [ ] B1: Spread within threshold -> PASS
- [ ] B2: Spread above threshold -> FAIL and veto candidate
- [ ] B3: OI below threshold -> FAIL and veto candidate

## C) Greeks and Straddle Logic
- [ ] C1: Bullish greek profile yields bullish score uplift
- [ ] C2: Bearish greek profile yields bearish score uplift
- [ ] C3: Straddle breakout upward supports CE bias
- [ ] C4: Straddle breakout downward supports PE bias

## D) Decision Merge
- [ ] D1: Momentum bullish + options bullish -> BUY_CALL
- [ ] D2: Momentum bullish + options bearish -> NO_TRADE or low confidence
- [ ] D3: Any risk veto -> NO_TRADE

## E) Output Integrity
- [ ] E1: Output schema always valid
- [ ] E2: Rationale always present
- [ ] E3: Risk checks always present
