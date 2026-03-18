from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from agents import ExecutionPlannerAgent, MarketRegimeAgent, OptionsStructureAgent, RiskOfficerAgent
from consensus import ConsensusPolicy
from contracts import ExecutionPlan, OrchestrationResult, Phase3Input
from memory import AgentMemory


def _load_input(path: Path) -> Phase3Input:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Phase3Input(**raw)


def _to_execution_plan(execution_agent_decision, consensus):
    if execution_agent_decision.action == "NO_TRADE":
        return ExecutionPlan(
            action="NO_TRADE",
            confidence="LOW",
            strike_zone="NONE",
            urgency="LOW",
            rationale=execution_agent_decision.rationale,
        )

    strike_zone = "OTM_1" if execution_agent_decision.confidence == "HIGH" else "ATM"
    urgency = "HIGH" if execution_agent_decision.confidence == "HIGH" else "MEDIUM"
    return ExecutionPlan(
        action=consensus.action,
        confidence=consensus.confidence,
        strike_zone=strike_zone,
        urgency=urgency,
        rationale=execution_agent_decision.rationale,
    )


def run_single(item: Phase3Input) -> OrchestrationResult:
    market_agent = MarketRegimeAgent()
    options_agent = OptionsStructureAgent()
    risk_agent = RiskOfficerAgent()
    execution_agent = ExecutionPlannerAgent()
    consensus_policy = ConsensusPolicy()
    memory = AgentMemory()

    memory.update(item.trend_strength, item.underlying_change_pct)

    market = market_agent.evaluate(item)
    options = options_agent.evaluate(item)
    risk = risk_agent.evaluate(item)
    consensus = consensus_policy.merge(market, options, risk)
    execution_decision = execution_agent.plan(consensus.action, consensus.confidence, consensus.weighted_score)
    execution_plan = _to_execution_plan(execution_decision, consensus)

    return OrchestrationResult(
        market_regime=market,
        options_structure=options,
        risk_officer=risk,
        consensus=consensus,
        execution_plan=execution_plan,
        memory_snapshot=memory.snapshot(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 3 multi-agent orchestration")
    parser.add_argument("--input", required=True, help="Path to Phase 3 input JSON")
    args = parser.parse_args()

    result = run_single(_load_input(Path(args.input)))
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
