from dataclasses import dataclass
from typing import Dict


@dataclass
class SignalConfig:
    bullish_threshold: float = 0.4
    bearish_threshold: float = -0.4
    strong_move_threshold: float = 0.8
    max_daily_loss_pct: float = 2.0
    max_spread_bps: float = 50.0
    stop_loss_pct: float = 12.0
    target_pct: float = 24.0
    model_version: str = "phase1_baseline_v1"


STRIKE_STEPS: Dict[str, int] = {
    "NIFTY50": 50,
    "BANKNIFTY": 100,
    "SENSEX": 100,
}
