from contracts import PositionSizingOutput


class PositionSizer:
    @staticmethod
    def _confidence_factor(confidence: str) -> float:
        mapping = {"HIGH": 1.0, "MEDIUM": 0.8, "LOW": 0.55}
        return mapping.get(confidence, 0.55)

    @staticmethod
    def _volatility_factor(iv_percentile: float) -> float:
        if iv_percentile <= 30:
            return 1.0
        if iv_percentile <= 60:
            return 0.85
        if iv_percentile <= 80:
            return 0.7
        return 0.55

    def compute(self, action: str, confidence: str, iv_percentile: float, time_multiplier: float) -> PositionSizingOutput:
        if action == "NO_TRADE":
            return PositionSizingOutput(multiplier=0.0, rationale="No position size for NO_TRADE action.")

        conf_factor = self._confidence_factor(confidence)
        vol_factor = self._volatility_factor(iv_percentile)
        multiplier = max(0.0, min(1.5, time_multiplier * conf_factor * vol_factor))

        return PositionSizingOutput(
            multiplier=round(multiplier, 2),
            rationale=(
                f"Sizing from time={time_multiplier:.2f}, confidence_factor={conf_factor:.2f}, "
                f"volatility_factor={vol_factor:.2f}."
            ),
        )
