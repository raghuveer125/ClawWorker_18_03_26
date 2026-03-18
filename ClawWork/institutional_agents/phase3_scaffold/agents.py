from __future__ import annotations

from contracts import AgentDecision, Phase3Input


class MarketRegimeAgent:
    def evaluate(self, item: Phase3Input) -> AgentDecision:
        if item.trend_strength >= 0.6 and item.underlying_change_pct >= 0.3:
            return AgentDecision(
                agent="market_regime",
                action="BUY_CALL",
                confidence="HIGH",
                score=80.0,
                rationale="Trend and price momentum both bullish.",
            )

        if item.trend_strength <= -0.6 and item.underlying_change_pct <= -0.3:
            return AgentDecision(
                agent="market_regime",
                action="BUY_PUT",
                confidence="HIGH",
                score=80.0,
                rationale="Trend and price momentum both bearish.",
            )

        if item.underlying_change_pct >= 0.2:
            return AgentDecision(
                agent="market_regime",
                action="BUY_CALL",
                confidence="MEDIUM",
                score=62.0,
                rationale="Mild bullish momentum in underlying.",
            )

        if item.underlying_change_pct <= -0.2:
            return AgentDecision(
                agent="market_regime",
                action="BUY_PUT",
                confidence="MEDIUM",
                score=62.0,
                rationale="Mild bearish momentum in underlying.",
            )

        return AgentDecision(
            agent="market_regime",
            action="NO_TRADE",
            confidence="LOW",
            score=40.0,
            rationale="No clear regime edge.",
        )


class OptionsStructureAgent:
    def evaluate(self, item: Phase3Input) -> AgentDecision:
        if not item.options_liquidity_ok or not item.options_spread_ok:
            return AgentDecision(
                agent="options_structure",
                action="NO_TRADE",
                confidence="LOW",
                score=30.0,
                rationale="Options structure weak: liquidity/spread filter failed.",
            )

        if item.options_bias == "BULLISH":
            return AgentDecision(
                agent="options_structure",
                action="BUY_CALL",
                confidence="HIGH" if item.iv_percentile < 75 else "MEDIUM",
                score=78.0,
                rationale="Options structure supports bullish continuation.",
            )

        if item.options_bias == "BEARISH":
            return AgentDecision(
                agent="options_structure",
                action="BUY_PUT",
                confidence="HIGH" if item.iv_percentile < 75 else "MEDIUM",
                score=78.0,
                rationale="Options structure supports bearish continuation.",
            )

        return AgentDecision(
            agent="options_structure",
            action="NO_TRADE",
            confidence="LOW",
            score=42.0,
            rationale="Options structure neutral.",
        )


class RiskOfficerAgent:
    def __init__(self, max_daily_loss_pct: float = -2.0):
        self.max_daily_loss_pct = max_daily_loss_pct

    def evaluate(self, item: Phase3Input) -> AgentDecision:
        if item.daily_realized_pnl_pct <= self.max_daily_loss_pct:
            return AgentDecision(
                agent="risk_officer",
                action="NO_TRADE",
                confidence="HIGH",
                score=100.0,
                rationale="Daily loss limit breached.",
                veto=True,
            )

        if item.event_risk_high:
            return AgentDecision(
                agent="risk_officer",
                action="NO_TRADE",
                confidence="HIGH",
                score=90.0,
                rationale="High event risk window active.",
                veto=True,
            )

        return AgentDecision(
            agent="risk_officer",
            action="NO_TRADE",
            confidence="LOW",
            score=20.0,
            rationale="No hard risk veto.",
            veto=False,
        )


class ExecutionPlannerAgent:
    def plan(self, action: str, confidence: str, weighted_score: float) -> AgentDecision:
        if action == "NO_TRADE":
            return AgentDecision(
                agent="execution_planner",
                action="NO_TRADE",
                confidence="LOW",
                score=weighted_score,
                rationale="No execution plan because consensus is NO_TRADE.",
            )

        urgency = "HIGH" if confidence == "HIGH" else "MEDIUM"
        return AgentDecision(
            agent="execution_planner",
            action=action,
            confidence=confidence,
            score=weighted_score,
            rationale=f"Execution plan prepared with {urgency} urgency.",
        )
