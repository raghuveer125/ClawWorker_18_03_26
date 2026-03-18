from dataclasses import dataclass
from typing import Dict, Literal


Action = Literal["BUY_CALL", "BUY_PUT", "NO_TRADE"]
Confidence = Literal["LOW", "MEDIUM", "HIGH"]
SessionSlot = Literal["OPEN", "MIDDAY", "CLOSE"]
Regime = Literal["TREND", "MEAN_REVERSION", "NEUTRAL"]


@dataclass
class Phase4Input:
    underlying: Literal["NIFTY50", "BANKNIFTY", "SENSEX"]
    timestamp: str
    session_slot: SessionSlot
    underlying_change_pct: float
    trend_strength: float
    iv_percentile: float
    options_bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    event_name: str = "NONE"
    event_window_active: bool = False
    current_portfolio_exposure_pct: float = 0.0
    max_portfolio_exposure_pct: float = 35.0


@dataclass
class RegimeOutput:
    regime: Regime
    confidence: Confidence
    rationale: str


@dataclass
class RiskFilterOutput:
    blocked: bool
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    rationale: str


@dataclass
class TimeBehaviorOutput:
    action_bias: Action
    confidence_adjustment: float
    position_size_multiplier: float
    rationale: str


@dataclass
class Phase4Decision:
    action: Action
    confidence: Confidence
    confidence_score: float
    position_size_multiplier: float
    rationale: str
    policy_tags: Dict[str, str]


@dataclass
class PositionSizingOutput:
    multiplier: float
    rationale: str


@dataclass
class PortfolioControlOutput:
    blocked: bool
    multiplier_cap: float
    utilization_pct: float
    rationale: str
