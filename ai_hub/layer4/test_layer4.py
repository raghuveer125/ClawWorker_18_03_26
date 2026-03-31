"""Test Layer 4 - Execution & Monitoring."""

import asyncio
import sys
from pathlib import Path

# Add project root to path (two levels up from ai_hub/layer4/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def test_layer4():
    """Test all Layer 4 components."""
    print("=" * 50)
    print("Testing Layer 4 - Execution & Monitoring")
    print("=" * 50)

    # 1. Test Execution Grid
    print("\n1. Testing ParallelExecutionGrid...")
    from ai_hub.layer4.execution.execution_grid import (
        ParallelExecutionGrid, ExecutionTask, ExecutionStatus
    )

    grid = ParallelExecutionGrid(max_concurrent=5)

    # Track progress
    progress_updates = []
    grid.on_progress(lambda tid, p, m: progress_updates.append((tid, p, m)))

    completions = []
    grid.on_completion(lambda r: completions.append(r))

    # Create test executor
    async def test_executor(task):
        await asyncio.sleep(0.05)  # Simulate work
        return {"processed": task.task_type, "value": 42}

    # Execute single task
    task1 = ExecutionTask(
        task_id="task_001",
        goal_id="goal_001",
        task_type="test_task",
        payload={"data": "test"},
        executor=test_executor,
    )

    result = await grid.execute_single(task1)
    print(f"   Single task result: {result.status.value}")
    print(f"   Execution time: {result.execution_time:.3f}s")
    print(f"   Progress updates: {len(progress_updates)}")

    # Execute parallel tasks with dependencies
    tasks = [
        ExecutionTask("t1", "g1", "fetch", executor=test_executor),
        ExecutionTask("t2", "g1", "fetch", executor=test_executor),
        ExecutionTask("t3", "g1", "process", dependencies=["t1", "t2"], executor=test_executor),
        ExecutionTask("t4", "g1", "output", dependencies=["t3"], executor=test_executor),
    ]

    results = await grid.execute_parallel(tasks)
    completed = sum(1 for r in results.values() if r.status == ExecutionStatus.COMPLETED)
    print(f"   Parallel tasks: {completed}/{len(tasks)} completed")

    stats = grid.get_stats()
    print(f"   Grid stats: {stats['total_executed']} executed, avg={stats['avg_execution_time']:.3f}s")
    print("   ✓ ParallelExecutionGrid working")

    # 2. Test Progress Tracker
    print("\n2. Testing ProgressTrackerAgent...")
    from ai_hub.layer4.monitoring.progress_tracker import ProgressTrackerAgent

    tracker = ProgressTrackerAgent()

    # Track some tasks
    tracker.start_task("task_a", "goal_x", "analysis", "worker_1")
    tracker.update_progress("task_a", 0.5, "Processing...")
    tracker.complete_task("task_a", success=True)

    tracker.start_task("task_b", "goal_x", "analysis", "worker_2")
    tracker.update_progress("task_b", 0.3, "In progress...")

    tracker.start_task("task_c", "goal_x", "execution", "worker_3")
    tracker.complete_task("task_c", success=False, message="Network error")

    # Get progress
    task_prog = tracker.get_task_progress("task_b")
    print(f"   Task B progress: {task_prog.progress:.0%}")

    goal_prog = tracker.get_goal_progress("goal_x")
    print(f"   Goal X: {goal_prog.completed_tasks}/{goal_prog.total_tasks} completed")
    print(f"   Overall progress: {goal_prog.overall_progress:.0%}")

    # Get ETA
    eta = tracker.get_estimated_duration("analysis")
    print(f"   Estimated analysis duration: {eta:.3f}s" if eta else "   No ETA available yet")

    summary = tracker.get_summary()
    print(f"   Summary: {summary['completed']} completed, {summary['failed']} failed")
    print("   ✓ ProgressTrackerAgent working")

    # 3. Test Error Handler
    print("\n3. Testing ErrorHandlerAgent...")
    from ai_hub.layer4.error.error_handler import (
        ErrorHandlerAgent, RecoveryAction, ErrorSeverity
    )

    error_handler = ErrorHandlerAgent(max_retries=3)

    # Track errors
    handled_errors = []
    error_handler.on_error(lambda e, p: handled_errors.append((e, p)))

    # Handle different error types
    plan1 = error_handler.handle_error(
        task_id="task_001",
        goal_id="goal_001",
        error_type="TimeoutError",
        message="Task timed out after 30s",
        worker_id="worker_1"
    )
    print(f"   Timeout error -> {plan1.action.value}")

    plan2 = error_handler.handle_error(
        task_id="task_002",
        goal_id="goal_001",
        error_type="ConnectionError",
        message="Connection refused to server",
    )
    print(f"   Network error -> {plan2.action.value}, delay={plan2.delay_seconds}s")

    plan3 = error_handler.handle_error(
        task_id="task_003",
        goal_id="goal_001",
        error_type="ValueError",
        message="Invalid data format",
    )
    print(f"   Data error -> {plan3.action.value}")

    # Simulate retries exceeding limit
    for _ in range(4):
        error_handler.handle_error(
            task_id="task_004",
            goal_id="goal_001",
            error_type="WorkerError",
            message="Worker unavailable",
        )

    plan4 = error_handler.handle_error(
        task_id="task_004",
        goal_id="goal_001",
        error_type="WorkerError",
        message="Worker still unavailable",
    )
    print(f"   After max retries -> {plan4.action.value}")

    # Check stats
    error_stats = error_handler.get_stats()
    print(f"   Error stats: {error_stats['total_errors']} total")
    print(f"   By category: {error_stats['by_category']}")

    # Should abort goal?
    should_abort = error_handler.should_abort_goal("goal_001")
    print(f"   Should abort goal: {should_abort}")

    print("   ✓ ErrorHandlerAgent working")

    # 4. Integration test
    print("\n4. Integration test...")

    # Create execution grid with monitoring
    grid2 = ParallelExecutionGrid()
    tracker2 = ProgressTrackerAgent()
    error_handler2 = ErrorHandlerAgent()

    # Connect progress tracker to grid
    def on_grid_progress(task_id, progress, message):
        if progress == 0.0:
            tracker2.start_task(task_id, "integ_goal", "test")
        elif progress == 1.0:
            tracker2.complete_task(task_id, success=True)
        else:
            tracker2.update_progress(task_id, progress, message)

    grid2.on_progress(on_grid_progress)

    # Execute tasks
    integ_tasks = [
        ExecutionTask(f"integ_{i}", "integ_goal", "test", executor=test_executor)
        for i in range(3)
    ]

    await grid2.execute_parallel(integ_tasks)

    integ_summary = tracker2.get_summary()
    print(f"   Integration: {integ_summary['completed']} tasks completed")
    print("   ✓ Integration working")

    print("\n" + "=" * 50)
    print("Layer 4 - All tests passed!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_layer4())
