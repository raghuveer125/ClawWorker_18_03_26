from contracts import Phase4Input, PortfolioControlOutput


class PortfolioExposureControl:
    def evaluate(self, item: Phase4Input, action: str) -> PortfolioControlOutput:
        utilization = 0.0
        if item.max_portfolio_exposure_pct > 0:
            utilization = (item.current_portfolio_exposure_pct / item.max_portfolio_exposure_pct) * 100.0

        if action == "NO_TRADE":
            return PortfolioControlOutput(
                blocked=False,
                multiplier_cap=1.0,
                utilization_pct=round(utilization, 2),
                rationale="No exposure check required for NO_TRADE.",
            )

        if item.current_portfolio_exposure_pct >= item.max_portfolio_exposure_pct:
            return PortfolioControlOutput(
                blocked=True,
                multiplier_cap=0.0,
                utilization_pct=round(utilization, 2),
                rationale="Portfolio exposure limit reached.",
            )

        if item.current_portfolio_exposure_pct >= 0.85 * item.max_portfolio_exposure_pct:
            return PortfolioControlOutput(
                blocked=False,
                multiplier_cap=0.5,
                utilization_pct=round(utilization, 2),
                rationale="Exposure near limit; capping position size.",
            )

        return PortfolioControlOutput(
            blocked=False,
            multiplier_cap=1.0,
            utilization_pct=round(utilization, 2),
            rationale="Exposure within limit.",
        )
