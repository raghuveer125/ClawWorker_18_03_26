from dataclasses import dataclass
from typing import Literal, Optional

Action = Literal["BUY_CALL", "BUY_PUT", "NO_TRADE"]
Confidence = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass
class MarketInput:
    timestamp: str
    underlying: Literal["NIFTY50", "BANKNIFTY", "SENSEX"]
    ltp: float
    prev_close: float
    session: Literal["OPEN", "MIDDAY", "CLOSE"]
    daily_realized_pnl_pct: float
    bid_ask_spread_bps: float


@dataclass
class RiskChecks:
    daily_loss_guard: Literal["PASS", "FAIL"]
    spread_guard: Literal["PASS", "FAIL"]
    data_quality_guard: Literal["PASS", "FAIL"]


@dataclass
class DecisionOutput:
    action: Action
    confidence: Confidence
    underlying: str
    preferred_strike: Optional[int]
    stop_loss_pct: Optional[float]
    target_pct: Optional[float]
    rationale: str
    risk_checks: RiskChecks
    model_version: str
