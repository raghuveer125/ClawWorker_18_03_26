from contracts import Phase4Decision, Phase4Input
from event_risk_filter import EventRiskFilter
from portfolio_controls import PortfolioExposureControl
from position_sizer import PositionSizer
from regime_model import RegimeModel
from time_behavior import TimeOfDayBehavior


def _base_action(item: Phase4Input) -> str:
    if item.options_bias == "BULLISH" and item.underlying_change_pct > 0:
        return "BUY_CALL"
    if item.options_bias == "BEARISH" and item.underlying_change_pct < 0:
        return "BUY_PUT"
    return "NO_TRADE"


def _score_to_confidence(score: float) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 55:
        return "MEDIUM"
    return "LOW"


class Phase4DecisionEngine:
    def __init__(self):
        self.regime_model = RegimeModel()
        self.event_filter = EventRiskFilter()
        self.time_behavior = TimeOfDayBehavior()
        self.position_sizer = PositionSizer()
        self.portfolio_control = PortfolioExposureControl()

    def evaluate(self, item: Phase4Input) -> Phase4Decision:
        regime = self.regime_model.classify(item)
        event = self.event_filter.evaluate(item)
        time_block = self.time_behavior.evaluate(item)

        if event.blocked:
            return Phase4Decision(
                action="NO_TRADE",
                confidence="HIGH",
                confidence_score=0.0,
                position_size_multiplier=0.0,
                rationale=f"Blocked by event filter. {event.rationale}",
                policy_tags={
                    "regime": regime.regime,
                    "event_risk": event.risk_level,
                    "time_slot": item.session_slot,
                    "gate": "EVENT_BLOCK",
                },
            )

        action = _base_action(item)
        score = 60.0

        if regime.regime == "TREND" and action != "NO_TRADE":
            score += 10.0
        elif regime.regime == "MEAN_REVERSION" and action != "NO_TRADE":
            score -= 8.0

        score += time_block.confidence_adjustment

        if action == "NO_TRADE":
            score = min(score, 52.0)

        score = max(0.0, min(100.0, score))
        confidence = _score_to_confidence(score)

        sizing = self.position_sizer.compute(
            action=action,
            confidence=confidence,
            iv_percentile=item.iv_percentile,
            time_multiplier=time_block.position_size_multiplier,
        )
        portfolio = self.portfolio_control.evaluate(item, action)

        if portfolio.blocked:
            return Phase4Decision(
                action="NO_TRADE",
                confidence="HIGH",
                confidence_score=0.0,
                position_size_multiplier=0.0,
                rationale=f"Blocked by portfolio control. {portfolio.rationale}",
                policy_tags={
                    "regime": regime.regime,
                    "event_risk": event.risk_level,
                    "time_slot": item.session_slot,
                    "gate": "PORTFOLIO_BLOCK",
                    "exposure_utilization_pct": f"{portfolio.utilization_pct}",
                },
            )

        final_multiplier = min(sizing.multiplier, portfolio.multiplier_cap)

        rationale = " | ".join(
            [
                regime.rationale,
                event.rationale,
                time_block.rationale,
                sizing.rationale,
                portfolio.rationale,
                f"Base action={action}, score={score:.2f}",
            ]
        )

        return Phase4Decision(
            action=action,
            confidence=confidence,
            confidence_score=round(score, 2),
            position_size_multiplier=round(final_multiplier, 2),
            rationale=rationale,
            policy_tags={
                "regime": regime.regime,
                "event_risk": event.risk_level,
                "time_slot": item.session_slot,
                "gate": "PASS",
                "exposure_utilization_pct": f"{portfolio.utilization_pct}",
            },
        )
