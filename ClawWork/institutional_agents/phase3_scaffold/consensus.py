from __future__ import annotations

from contracts import AgentDecision, ConsensusDecision


def _confidence_from_score(score: float) -> str:
    if score >= 72:
        return "HIGH"
    if score >= 58:
        return "MEDIUM"
    return "LOW"


class ConsensusPolicy:
    def __init__(self, market_weight: float = 0.45, options_weight: float = 0.55):
        self.market_weight = market_weight
        self.options_weight = options_weight

    @staticmethod
    def _action_to_signed(action: str, score: float) -> float:
        if action == "BUY_CALL":
            return score
        if action == "BUY_PUT":
            return -score
        return 0.0

    def merge(
        self,
        market_decision: AgentDecision,
        options_decision: AgentDecision,
        risk_decision: AgentDecision,
    ) -> ConsensusDecision:
        if risk_decision.veto:
            return ConsensusDecision(
                action="NO_TRADE",
                confidence="HIGH",
                weighted_score=0.0,
                vote_breakdown={
                    "market": market_decision.score,
                    "options": options_decision.score,
                    "risk_veto": 1.0,
                },
                rationale=f"Risk veto applied: {risk_decision.rationale}",
                veto_applied=True,
            )

        market_signed = self._action_to_signed(market_decision.action, market_decision.score)
        options_signed = self._action_to_signed(options_decision.action, options_decision.score)

        weighted_signed = (self.market_weight * market_signed) + (self.options_weight * options_signed)
        abs_score = abs(weighted_signed)

        if abs_score < 50.0:
            return ConsensusDecision(
                action="NO_TRADE",
                confidence="LOW",
                weighted_score=round(abs_score, 2),
                vote_breakdown={
                    "market_signed": round(market_signed, 2),
                    "options_signed": round(options_signed, 2),
                    "weighted_signed": round(weighted_signed, 2),
                },
                rationale="Consensus too weak or conflicting.",
                veto_applied=False,
            )

        action = "BUY_CALL" if weighted_signed > 0 else "BUY_PUT"
        confidence = _confidence_from_score(abs_score)
        return ConsensusDecision(
            action=action,
            confidence=confidence,
            weighted_score=round(abs_score, 2),
            vote_breakdown={
                "market_signed": round(market_signed, 2),
                "options_signed": round(options_signed, 2),
                "weighted_signed": round(weighted_signed, 2),
            },
            rationale="Consensus formed from weighted market/options votes.",
            veto_applied=False,
        )
