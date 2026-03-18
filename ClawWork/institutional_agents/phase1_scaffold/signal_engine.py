from config import STRIKE_STEPS, SignalConfig
from models import DecisionOutput, MarketInput
from risk import evaluate_risk_guards, has_veto


class SignalEngine:
    def __init__(self, config: SignalConfig | None = None):
        self.config = config or SignalConfig()

    @staticmethod
    def _change_pct(ltp: float, prev_close: float) -> float:
        return ((ltp - prev_close) / prev_close) * 100.0

    @staticmethod
    def _round_to_step(value: float, step: int) -> int:
        return int(round(value / step) * step)

    def _pick_confidence(self, abs_change: float) -> str:
        if abs_change >= self.config.strong_move_threshold:
            return "HIGH"
        if abs_change >= abs(self.config.bullish_threshold):
            return "MEDIUM"
        return "LOW"

    def _pick_strike(self, underlying: str, ltp: float, action: str) -> int | None:
        step = STRIKE_STEPS.get(underlying, 100)
        atm = self._round_to_step(ltp, step)
        if action == "BUY_CALL":
            return atm + step
        if action == "BUY_PUT":
            return atm - step
        return None

    def decide(self, market: MarketInput) -> DecisionOutput:
        risk_checks = evaluate_risk_guards(market, self.config)
        if has_veto(risk_checks):
            return DecisionOutput(
                action="NO_TRADE",
                confidence="LOW",
                underlying=market.underlying,
                preferred_strike=None,
                stop_loss_pct=None,
                target_pct=None,
                rationale="Risk veto triggered.",
                risk_checks=risk_checks,
                model_version=self.config.model_version,
            )

        change_pct = self._change_pct(market.ltp, market.prev_close)
        abs_change = abs(change_pct)

        if change_pct >= self.config.bullish_threshold:
            action = "BUY_CALL"
            rationale = f"Bullish momentum detected ({change_pct:.2f}%)."
        elif change_pct <= self.config.bearish_threshold:
            action = "BUY_PUT"
            rationale = f"Bearish momentum detected ({change_pct:.2f}%)."
        else:
            action = "NO_TRADE"
            rationale = f"No clear momentum edge ({change_pct:.2f}%)."

        confidence = self._pick_confidence(abs_change)
        preferred_strike = self._pick_strike(market.underlying, market.ltp, action)

        stop_loss_pct = self.config.stop_loss_pct if action != "NO_TRADE" else None
        target_pct = self.config.target_pct if action != "NO_TRADE" else None

        return DecisionOutput(
            action=action,
            confidence=confidence,
            underlying=market.underlying,
            preferred_strike=preferred_strike,
            stop_loss_pct=stop_loss_pct,
            target_pct=target_pct,
            rationale=rationale,
            risk_checks=risk_checks,
            model_version=self.config.model_version,
        )
