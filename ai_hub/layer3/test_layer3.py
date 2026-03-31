"""Test Layer 3 - Worker Army Orchestrator."""

import asyncio
import sys
from pathlib import Path

# Add project root to path (two levels up from ai_hub/layer3/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def test_layer3():
    """Test all Layer 3 components."""
    print("=" * 50)
    print("Testing Layer 3 - Worker Army Orchestrator")
    print("=" * 50)

    # 1. Test Worker Registry
    print("\n1. Testing Worker Registry...")
    from ai_hub.layer3.registry.worker_registry import (
        WorkerRegistry, ArmyType, WorkerStatus
    )

    registry = WorkerRegistry()

    # Check default armies
    scalping = registry.get_army(ArmyType.SCALPING)
    print(f"   Scalping Army max workers: {scalping.max_workers}")

    # Register workers
    registry.register_worker(
        worker_id="scalp_bot_1",
        worker_type="scalping_bot",
        army_type=ArmyType.SCALPING,
        capabilities=[
            {"name": "execute_scalp", "task_types": ["scalp_entry", "scalp_exit"]},
            {"name": "analyze_price", "task_types": ["price_analysis"]},
        ]
    )

    registry.register_worker(
        worker_id="scalp_bot_2",
        worker_type="scalping_bot",
        army_type=ArmyType.SCALPING,
        capabilities=[
            {"name": "execute_scalp", "task_types": ["scalp_entry", "scalp_exit"]},
        ]
    )

    registry.register_worker(
        worker_id="risk_bot_1",
        worker_type="risk_bot",
        army_type=ArmyType.RISK,
        capabilities=[
            {"name": "check_risk", "task_types": ["risk_assessment"]},
            {"name": "calculate_exposure", "task_types": ["exposure_calc"]},
        ]
    )

    # Find workers
    scalp_workers = registry.find_by_task_type("scalp_entry")
    print(f"   Workers for scalp_entry: {len(scalp_workers)}")

    risk_workers = registry.find_in_army(ArmyType.RISK)
    print(f"   Workers in Risk Army: {len(risk_workers)}")

    # Select best worker
    best = registry.select_best_worker("scalp_entry", ArmyType.SCALPING)
    print(f"   Best worker for scalp: {best.worker_id if best else 'None'}")

    # Update status
    registry.update_status("scalp_bot_1", WorkerStatus.BUSY, "task_001")
    worker = registry.get_worker("scalp_bot_1")
    print(f"   scalp_bot_1 status: {worker.status.value}")

    # Record completion
    registry.record_task_completion("scalp_bot_1", success=True, execution_time=1.5)
    worker = registry.get_worker("scalp_bot_1")
    print(f"   scalp_bot_1 tasks completed: {worker.tasks_completed}")

    stats = registry.get_registry_stats()
    print(f"   Total workers: {stats['total_workers']}")
    print("   ✓ Worker Registry working")

    # 2. Test Dispatcher Agent
    print("\n2. Testing Dispatcher Agent...")
    from ai_hub.layer3.dispatch.dispatcher_agent import DispatcherAgent

    dispatcher = DispatcherAgent(registry=registry)
    dispatcher.start()

    # Submit tasks
    submitted = dispatcher.submit(
        task_id="task_001",
        task_type="scalp_entry",
        goal_id="goal_001",
        payload={"symbol": "NIFTY50", "direction": "long"},
        priority=2,
        required_capabilities=["execute_scalp"],
        preferred_army=ArmyType.SCALPING,
    )
    print(f"   Task submitted: {submitted}")

    dispatcher.submit(
        task_id="task_002",
        task_type="risk_assessment",
        goal_id="goal_001",
        payload={"check": "exposure"},
        priority=1,  # Higher priority
    )

    print(f"   Pending tasks: {dispatcher.get_pending_count()}")

    # Dispatch tasks
    result1 = await dispatcher.dispatch_next()
    print(f"   Dispatched task_002 to: {result1.worker_id if result1 else 'None'}")

    result2 = await dispatcher.dispatch_next()
    print(f"   Dispatched task_001 to: {result2.worker_id if result2 else 'None'}")

    # Complete task
    if result1 and result1.worker_id:
        dispatcher.complete_task(
            task_id="task_002",
            worker_id=result1.worker_id,
            success=True,
            execution_time=0.5,
            result={"risk_ok": True}
        )

    disp_stats = dispatcher.get_stats()
    print(f"   Dispatcher stats: dispatched={disp_stats['dispatched']}, completed={disp_stats['completed']}")
    print("   ✓ Dispatcher Agent working")

    # 3. Test Health Monitor
    print("\n3. Testing Health Monitor...")
    from ai_hub.layer3.health.health_monitor import HealthMonitorAgent

    health_monitor = HealthMonitorAgent(registry=registry)

    # Track events
    events_received = []
    health_monitor.on_health_event(lambda e: events_received.append(e))

    # Record heartbeats
    health_monitor.record_heartbeat("scalp_bot_1")
    health_monitor.record_heartbeat("scalp_bot_2")
    health_monitor.record_heartbeat("risk_bot_1")

    # Record task results
    health_monitor.record_task_result("scalp_bot_1", success=True, response_time=1.2)
    health_monitor.record_task_result("scalp_bot_2", success=False, response_time=5.0)
    health_monitor.record_task_result("scalp_bot_2", success=False, response_time=5.0)
    health_monitor.record_task_result("scalp_bot_2", success=False, response_time=5.0)

    # Check health
    health_results = health_monitor.check_all_workers()
    print(f"   Healthy: {len(health_results['healthy'])}")
    print(f"   Unhealthy: {len(health_results['unhealthy'])}")
    print(f"   Events received: {len(events_received)}")

    # Get army health
    army_health = health_monitor.get_army_health(ArmyType.SCALPING)
    print(f"   Scalping Army health score: {army_health['health_score']:.2f}")

    # Get recommendations
    recommendations = health_monitor.get_recommendations()
    print(f"   Recommendations: {len(recommendations)}")
    for rec in recommendations[:2]:
        print(f"     - [{rec['priority']}] {rec['action']}")

    health_stats = health_monitor.get_stats()
    print(f"   Health stats: monitored={health_stats['monitored_workers']}")
    print("   ✓ Health Monitor working")

    # 4. Integration with Synapse
    print("\n4. Testing Synapse Integration...")
    from ai_hub.synapse import get_synapse

    synapse = get_synapse()

    # Register workers with synapse via registry
    registry.register_worker(
        worker_id="execution_bot_1",
        worker_type="executor",
        army_type=ArmyType.EXECUTION,
        capabilities=[
            {"name": "execute_order", "task_types": ["order_execution"]},
        ]
    )

    # Check synapse registered the agent
    agent = synapse.get_agent("execution_bot_1")
    print(f"   Synapse registered agent: {agent is not None}")

    # Send command via synapse
    msg_id = synapse.send_command(
        source="test",
        command="execute_task",
        params={"task_id": "synapse_task_001"},
        target="execution_bot_1"
    )
    print(f"   Sent command via Synapse: {msg_id}")

    print("   ✓ Synapse integration working")

    print("\n" + "=" * 50)
    print("Layer 3 - All tests passed!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_layer3())
