"""
Layer 1 Integration Test - Goal Decomposition
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_hub.layer1.goal.goal_manager import GoalManager, GoalType, GoalStatus
from ai_hub.layer1.agents.goal_parser_agent import GoalParserAgent
from ai_hub.layer1.agents.task_planner_agent import TaskPlannerAgent
from ai_hub.layer1.agents.prioritizer_agent import PrioritizerAgent
from ai_hub.layer1.agents.data_checker_agent import DataCheckerAgent


async def test_goal_manager():
    """Test GoalManager."""
    print("\n=== Testing GoalManager ===")

    gm = GoalManager()

    # Create goal
    goal = gm.create_goal("Scalp NIFTY with momentum strategy")
    print(f"Created goal: {goal.goal_id}")
    assert goal.status == GoalStatus.PENDING

    # Update goal
    gm.set_status(goal.goal_id, GoalStatus.PARSING)
    assert gm.get_goal(goal.goal_id).status == GoalStatus.PARSING

    # Get stats
    stats = gm.get_stats()
    print(f"Stats: {stats['total_goals']} total goals")

    print("GoalManager: PASSED")
    return True


async def test_goal_parser():
    """Test GoalParserAgent."""
    print("\n=== Testing GoalParserAgent ===")

    gm = GoalManager()
    parser = GoalParserAgent(goal_manager=gm, use_debate=False)

    # Test trade goal
    goal1 = gm.create_goal("Execute scalping on BANKNIFTY with momentum breakout")
    parsed1 = await parser.parse(goal1)
    print(f"Trade goal: type={parsed1.goal_type.value}, targets={parsed1.targets}")
    assert parsed1.goal_type == GoalType.TRADE
    assert "BANKNIFTY" in parsed1.targets

    # Test analyze goal
    goal2 = gm.create_goal("Analyze NIFTY market structure today")
    parsed2 = await parser.parse(goal2)
    print(f"Analyze goal: type={parsed2.goal_type.value}, targets={parsed2.targets}")
    assert parsed2.goal_type == GoalType.ANALYZE

    # Test optimize goal
    goal3 = gm.create_goal("Optimize strike selection parameters")
    parsed3 = await parser.parse(goal3)
    print(f"Optimize goal: type={parsed3.goal_type.value}")
    assert parsed3.goal_type == GoalType.OPTIMIZE

    print("GoalParserAgent: PASSED")
    return True


async def test_task_planner():
    """Test TaskPlannerAgent."""
    print("\n=== Testing TaskPlannerAgent ===")

    gm = GoalManager()
    parser = GoalParserAgent(goal_manager=gm, use_debate=False)
    planner = TaskPlannerAgent(goal_manager=gm)

    # Create and parse goal
    goal = gm.create_goal("Trade NIFTY with breakout strategy")
    await parser.parse(goal)

    # Plan tasks
    tasks = await planner.plan(goal)
    print(f"Created {len(tasks)} tasks")
    assert len(tasks) > 0

    # Check dependencies
    for task in tasks:
        print(f"  - {task.name} ({task.worker_type})")

    # Get execution order
    order = planner.get_execution_order(tasks)
    print(f"Execution phases: {len(order)}")

    # Visualize
    viz = planner.visualize_plan(tasks)
    print(viz[:200] + "...")

    print("TaskPlannerAgent: PASSED")
    return True


async def test_prioritizer():
    """Test PrioritizerAgent."""
    print("\n=== Testing PrioritizerAgent ===")

    gm = GoalManager()
    parser = GoalParserAgent(goal_manager=gm, use_debate=False)
    planner = TaskPlannerAgent(goal_manager=gm)
    prioritizer = PrioritizerAgent(goal_manager=gm)

    # Create multiple goals
    goals = [
        gm.create_goal("Trade BANKNIFTY urgently"),
        gm.create_goal("Analyze NIFTY structure"),
        gm.create_goal("Optimize parameters for tomorrow"),
    ]

    # Parse and plan all
    for goal in goals:
        await parser.parse(goal)
        await planner.plan(goal)

    # Prioritize
    for goal in goals:
        priority = await prioritizer.prioritize(goal)
        print(f"Goal {goal.goal_id[:8]}: priority={priority}, type={goal.goal_type.value}")

    # Get priority queue
    queue = prioritizer.get_priority_queue(goals)
    print(f"Priority queue: {[g.priority for g in queue]}")

    # Highest priority should be trade
    assert queue[0].goal_type == GoalType.TRADE

    print("PrioritizerAgent: PASSED")
    return True


async def test_data_checker():
    """Test DataCheckerAgent."""
    print("\n=== Testing DataCheckerAgent ===")

    gm = GoalManager()
    parser = GoalParserAgent(goal_manager=gm, use_debate=False)
    planner = TaskPlannerAgent(goal_manager=gm)
    checker = DataCheckerAgent(goal_manager=gm, auto_request=False)

    # Create and process goal
    goal = gm.create_goal("Analyze NIFTY with VWAP")
    await parser.parse(goal)
    await planner.plan(goal)

    # Check data
    all_available, missing = await checker.check(goal)
    print(f"Data check: available={all_available}, missing={missing[:3]}...")

    # Get summary
    summary = checker.get_data_summary(goal)
    print(f"Summary: {summary['total_required']} required, {len(summary['available'])} available")

    print("DataCheckerAgent: PASSED")
    return True


async def test_full_pipeline():
    """Test full Layer 1 pipeline."""
    print("\n=== Testing Full Pipeline ===")

    gm = GoalManager()
    parser = GoalParserAgent(goal_manager=gm, use_debate=False)
    planner = TaskPlannerAgent(goal_manager=gm)
    prioritizer = PrioritizerAgent(goal_manager=gm)
    checker = DataCheckerAgent(goal_manager=gm, auto_request=True)

    # Create goal
    goal = gm.create_goal("Execute momentum scalping on BANKNIFTY CE options")
    print(f"Created goal: {goal.goal_id}")

    # Parse
    parsed = await parser.parse(goal)
    print(f"Parsed: type={parsed.goal_type.value}, targets={parsed.targets}")

    # Plan
    tasks = await planner.plan(goal)
    print(f"Planned: {len(tasks)} tasks")

    # Prioritize
    priority = await prioritizer.prioritize(goal)
    print(f"Priority: {priority}")

    # Check data
    available, missing = await checker.check(goal)
    print(f"Data: ready={available}, missing={len(missing)}")

    # Final status
    final_goal = gm.get_goal(goal.goal_id)
    print(f"Final status: {final_goal.status.value}")

    # Should be READY or BLOCKED
    assert final_goal.status in [GoalStatus.READY, GoalStatus.BLOCKED]

    print("\nFull Pipeline: PASSED")
    return True


def main():
    print("=" * 60)
    print("LAYER 1 GOAL DECOMPOSITION - INTEGRATION TEST")
    print("=" * 60)

    results = {}
    results["goal_manager"] = asyncio.run(test_goal_manager())
    results["goal_parser"] = asyncio.run(test_goal_parser())
    results["task_planner"] = asyncio.run(test_task_planner())
    results["prioritizer"] = asyncio.run(test_prioritizer())
    results["data_checker"] = asyncio.run(test_data_checker())
    results["full_pipeline"] = asyncio.run(test_full_pipeline())

    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)

    all_passed = True
    for name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("ALL TESTS PASSED!")
    else:
        print("SOME TESTS FAILED!")
        sys.exit(1)


if __name__ == "__main__":
    main()
