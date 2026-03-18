from dataclasses import dataclass
from typing import Dict, List, Literal, Optional


Action = Literal["BUY_CALL", "BUY_PUT", "NO_TRADE"]
Confidence = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass
class Phase3Input:
    underlying: Literal["NIFTY50", "BANKNIFTY", "SENSEX"]
    timestamp: str
    underlying_change_pct: float
    trend_strength: float
    iv_percentile: float
    options_bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    options_liquidity_ok: bool
    options_spread_ok: bool
    daily_realized_pnl_pct: float
    event_risk_high: bool = False


@dataclass
class AgentDecision:
    agent: str
    action: Action
    confidence: Confidence
    score: float
    rationale: str
    veto: bool = False


@dataclass
class ConsensusDecision:
    action: Action
    confidence: Confidence
    weighted_score: float
    vote_breakdown: Dict[str, float]
    rationale: str
    veto_applied: bool


@dataclass
class ExecutionPlan:
    action: Action
    confidence: Confidence
    strike_zone: Literal["ITM_1", "ATM", "OTM_1", "NONE"]
    urgency: Literal["LOW", "MEDIUM", "HIGH"]
    rationale: str


@dataclass
class OrchestrationResult:
    market_regime: AgentDecision
    options_structure: AgentDecision
    risk_officer: AgentDecision
    consensus: ConsensusDecision
    execution_plan: ExecutionPlan
    memory_snapshot: Dict[str, object]


@dataclass
class MemoryState:
    key: str
    values: List[float]
    max_size: int = 20

    def append(self, value: float) -> None:
        self.values.append(float(value))
        if len(self.values) > self.max_size:
            self.values = self.values[-self.max_size:]

    def average(self) -> Optional[float]:
        if not self.values:
            return None
        return sum(self.values) / len(self.values)
