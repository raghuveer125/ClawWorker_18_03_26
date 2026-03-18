from contracts import Phase4Input, RiskFilterOutput


class EventRiskFilter:
    HIGH_RISK_EVENTS = {"RBI", "FED", "CPI", "EARNINGS"}

    def evaluate(self, item: Phase4Input) -> RiskFilterOutput:
        if item.event_window_active and item.event_name.upper() in self.HIGH_RISK_EVENTS:
            return RiskFilterOutput(
                blocked=True,
                risk_level="HIGH",
                rationale=f"Event-risk block active for {item.event_name} window.",
            )

        if item.event_window_active:
            return RiskFilterOutput(
                blocked=False,
                risk_level="MEDIUM",
                rationale="Event window active but not in high-risk block list.",
            )

        return RiskFilterOutput(
            blocked=False,
            risk_level="LOW",
            rationale="No elevated event-risk window.",
        )
