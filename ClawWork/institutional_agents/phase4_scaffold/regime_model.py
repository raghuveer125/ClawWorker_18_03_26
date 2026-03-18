from contracts import Phase4Input, RegimeOutput


class RegimeModel:
    def classify(self, item: Phase4Input) -> RegimeOutput:
        if abs(item.trend_strength) >= 0.65 and abs(item.underlying_change_pct) >= 0.35:
            return RegimeOutput(
                regime="TREND",
                confidence="HIGH",
                rationale="Strong trend and directional move detected.",
            )

        if abs(item.trend_strength) <= 0.25 and abs(item.underlying_change_pct) <= 0.2:
            return RegimeOutput(
                regime="MEAN_REVERSION",
                confidence="MEDIUM",
                rationale="Weak trend and muted move suggest mean reversion.",
            )

        return RegimeOutput(
            regime="NEUTRAL",
            confidence="LOW",
            rationale="No clear trend-day or mean-reversion signature.",
        )
