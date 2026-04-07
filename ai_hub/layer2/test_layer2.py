"""Test Layer 2 - Reasoning Engine components."""

import asyncio
import sys
from pathlib import Path

# Add project root to path (two levels up from ai_hub/layer2/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def test_layer2():
    """Test all Layer 2 components."""
    print("=" * 50)
    print("Testing Layer 2 - Reasoning Engine")
    print("=" * 50)

    # 1. Test ContextAgent
    print("\n1. Testing ContextAgent...")
    from ai_hub.layer2.context.context_agent import ContextAgent, ContextSnapshot

    context_agent = ContextAgent()
    context = await context_agent.get_context(
        targets=["NIFTY50"],
        goal_type="trade",
        include_positions=True
    )

    print(f"   Context timestamp: {context.timestamp}")
    print(f"   Regime: {context.regime}")
    print(f"   Volatility: {context.volatility_state}")
    print(f"   Market data keys: {list(context.market_data.keys())}")
    print(f"   Summary:\n{context_agent.get_context_summary(context)}")
    print("   ✓ ContextAgent working")

    # 2. Test MemoryAgent
    print("\n2. Testing MemoryAgent...")
    from ai_hub.layer2.memory.memory_agent import MemoryAgent, MemoryType

    memory_agent = MemoryAgent()

    # Store some memories
    goal_mem_id = memory_agent.store_goal_outcome(
        goal_id="test-001",
        goal_type="trade",
        success=True,
        pnl=500.0,
        regime="trending_up",
        parameters={"strategy": "breakout"}
    )
    print(f"   Stored goal outcome: {goal_mem_id}")

    fail_mem_id = memory_agent.store_failure_pattern(
        pattern_name="high_vol_loss",
        conditions={"volatility": "extreme", "regime": "ranging"},
        failure_type="stop_hit",
        occurrences=3,
        mitigation="Reduce position size"
    )
    print(f"   Stored failure pattern: {fail_mem_id}")

    strategy_mem_id = memory_agent.store_success_strategy(
        strategy_name="vwap_breakout",
        regime="trending_up",
        parameters={"entry_above_vwap": True, "stop_atr_mult": 1.5},
        win_rate=0.72,
        avg_pnl=350.0,
        sample_size=25
    )
    print(f"   Stored success strategy: {strategy_mem_id}")

    # Recall memories
    memories = memory_agent.recall_for_context("trade", "trending_up", ["NIFTY50"])
    print(f"   Recalled memories:")
    for category, mems in memories.items():
        print(f"     {category}: {len(mems)} entries")

    stats = memory_agent.get_stats()
    print(f"   Memory stats: {stats}")
    print("   ✓ MemoryAgent working")

    # 3. Test ReasonAgent
    print("\n3. Testing ReasonAgent...")
    from ai_hub.layer2.reasoning.reason_agent import ReasonAgent

    reason_agent = ReasonAgent(context_agent, memory_agent)

    result = await reason_agent.reason(
        goal_type="trade",
        targets=["NIFTY50"],
        parameters={"strategy": "breakout", "lots": 2},
        context=context
    )

    print(f"   Observations: {len(result.observations)}")
    print(f"   Analyses: {len(result.analyses)}")
    print(f"   Hypotheses: {len(result.hypotheses)}")
    print(f"   Confidence: {result.confidence.value}")
    if result.recommendation:
        print(f"   Recommendation: {result.recommendation.action}")
        print(f"   Rec confidence: {result.recommendation.confidence:.2f}")
    if result.warnings:
        print(f"   Warnings: {result.warnings}")
    print("   ✓ ReasonAgent working")

    # 4. Test CoTPipeline
    print("\n4. Testing CoTPipeline...")
    from ai_hub.layer2.reasoning.cot_pipeline import CoTPipeline, ReasoningPhase

    pipeline = CoTPipeline(context_agent, memory_agent, reason_agent)

    # Add progress callback
    def on_progress(step):
        status = "✓" if step.success else "✗"
        print(f"   [{status}] {step.phase.value}: {step.description} ({step.duration_ms:.0f}ms)")

    pipeline.on_progress(on_progress)

    pipeline_result = await pipeline.run(
        goal_id="goal-test-001",
        goal_type="trade",
        targets=["NIFTY50"],
        parameters={"strategy": "breakout", "lots": 1}
    )

    print(f"\n   Pipeline result:")
    print(f"     Decision: {pipeline_result.decision}")
    print(f"     Should proceed: {pipeline_result.should_proceed}")
    print(f"     Total duration: {pipeline_result.total_duration_ms:.0f}ms")
    print(f"     Steps completed: {len(pipeline_result.steps)}")
    print("   ✓ CoTPipeline working")

    # 5. Integration test with reason explanation
    print("\n5. Reasoning explanation...")
    explanation = reason_agent.explain(result)
    print(explanation)

    print("\n" + "=" * 50)
    print("Layer 2 - All tests passed!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_layer2())
