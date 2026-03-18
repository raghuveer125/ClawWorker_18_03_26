from contracts import Phase4Input, TimeBehaviorOutput


class TimeOfDayBehavior:
    def evaluate(self, item: Phase4Input) -> TimeBehaviorOutput:
        if item.session_slot == "OPEN":
            return TimeBehaviorOutput(
                action_bias="NO_TRADE",
                confidence_adjustment=-8.0,
                position_size_multiplier=0.7,
                rationale="Opening volatility: lower conviction and smaller size.",
            )

        if item.session_slot == "MIDDAY":
            return TimeBehaviorOutput(
                action_bias="NO_TRADE",
                confidence_adjustment=2.0,
                position_size_multiplier=1.0,
                rationale="Midday stability: neutral bias and normal sizing.",
            )

        return TimeBehaviorOutput(
            action_bias="NO_TRADE",
            confidence_adjustment=6.0,
            position_size_multiplier=0.9,
            rationale="Closing hour: confidence slightly higher, size slightly reduced.",
        )
