from dataclasses import dataclass
from typing import Dict, List, Literal


Action = Literal["BUY_CALL", "BUY_PUT", "NO_TRADE"]


@dataclass
class FeatureFlags:
    institutional_agent_enabled: bool = False
    shadow_mode_enabled: bool = True


@dataclass
class ShadowRow:
    timestamp: str
    underlying: Literal["NIFTY50", "BANKNIFTY", "SENSEX"]
    baseline_action: Action
    institutional_action: Action
    baseline_confidence: Literal["LOW", "MEDIUM", "HIGH"]
    institutional_confidence: Literal["LOW", "MEDIUM", "HIGH"]
    realized_outcome_action: Action


@dataclass
class ShadowComparisonResult:
    total_rows: int
    agreement_count: int
    disagreement_count: int
    agreement_pct: float
    institutional_better_count: int
    baseline_better_count: int


@dataclass
class RolloutStage:
    name: str
    allocation_pct: int
    pass_required: bool


@dataclass
class GoLiveGateResult:
    passed: bool
    checks: Dict[str, bool]
    reasons: List[str]
