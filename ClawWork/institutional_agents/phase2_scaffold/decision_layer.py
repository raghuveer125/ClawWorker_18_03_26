from contracts import FinalDecision, MomentumSignal, OptionsSignal


class DecisionLayer:
    @staticmethod
    def _momentum_score(momentum: MomentumSignal) -> float:
        if momentum.action in {"BUY_CALL", "BUY_PUT"}:
            return 80.0 if momentum.confidence == "HIGH" else 65.0
        return 30.0

    def merge(self, momentum: MomentumSignal, options_signal: OptionsSignal) -> FinalDecision:
        momentum_score = self._momentum_score(momentum)
        options_score = options_signal.options_score
        final_weighted_score = round((0.45 * momentum_score) + (0.55 * options_score), 2)

        score_breakdown = {
            "momentum": round(momentum_score, 2),
            "options": round(options_score, 2),
            "final_weighted": final_weighted_score,
        }

        if options_signal.signal == "NO_TRADE":
            return FinalDecision(
                action="NO_TRADE",
                confidence="LOW",
                final_weighted_score=final_weighted_score,
                score_breakdown=score_breakdown,
                rationale="Options guardrail veto.",
            )

        if momentum.action == "BUY_CALL" and options_signal.signal == "BULLISH":
            return FinalDecision(
                action="BUY_CALL",
                confidence="HIGH" if options_signal.confidence == "HIGH" else "MEDIUM",
                final_weighted_score=final_weighted_score,
                score_breakdown=score_breakdown,
                rationale="Momentum and options signals aligned bullish.",
            )

        if momentum.action == "BUY_PUT" and options_signal.signal == "BEARISH":
            return FinalDecision(
                action="BUY_PUT",
                confidence="HIGH" if options_signal.confidence == "HIGH" else "MEDIUM",
                final_weighted_score=final_weighted_score,
                score_breakdown=score_breakdown,
                rationale="Momentum and options signals aligned bearish.",
            )

        return FinalDecision(
            action="NO_TRADE",
            confidence="LOW",
            final_weighted_score=final_weighted_score,
            score_breakdown=score_breakdown,
            rationale="Momentum and options signals not aligned.",
        )
