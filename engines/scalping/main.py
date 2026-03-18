"""
Bot Army - Main Entry Point
Run pipelines, manage bots, and monitor trading.
"""

import asyncio
import argparse
import logging
from pathlib import Path

from orchestrator import BotOrchestrator, create_event_bus
from bots import (
    # Code Quality
    GuardianBot,
    SelfHealingBot,
    # Trading Core
    BacktestBot,
    RegimeBot,
    RiskSentinelBot,
    ExecutionBot,
    # Advanced (Quant Fund Edge)
    MetaStrategyBot,
    CorrelationBot,
    AlphaDecayBot,
    ExperimentBot,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="Bot Army - Trading & Code Automation")
    parser.add_argument("command", choices=["run", "list", "status"], help="Command to execute")
    parser.add_argument("--pipeline", "-p", help="Pipeline ID to run")
    parser.add_argument("--project", help="Project path for code bots")
    parser.add_argument("--symbol", default="NSE:NIFTY50-INDEX", help="Trading symbol")
    parser.add_argument("--redis", help="Redis URL for distributed event bus")

    args = parser.parse_args()

    # Create event bus
    if args.redis:
        event_bus = create_event_bus("redis", redis_url=args.redis)
    else:
        event_bus = create_event_bus("memory")

    # Create orchestrator
    orchestrator = BotOrchestrator(event_bus=event_bus)

    # Register all bots
    # Code Quality
    orchestrator.register_bot(GuardianBot)
    orchestrator.register_bot(SelfHealingBot)
    # Trading Core
    orchestrator.register_bot(BacktestBot)
    orchestrator.register_bot(RegimeBot)
    orchestrator.register_bot(RiskSentinelBot)
    orchestrator.register_bot(ExecutionBot)
    # Advanced (Quant Fund Edge)
    orchestrator.register_bot(MetaStrategyBot)
    orchestrator.register_bot(CorrelationBot)
    orchestrator.register_bot(AlphaDecayBot)
    orchestrator.register_bot(ExperimentBot)

    # Start orchestrator
    await orchestrator.start()

    try:
        if args.command == "list":
            print("\n=== Registered Pipelines ===")
            for pipeline in orchestrator.list_pipelines():
                print(f"  - {pipeline.pipeline_id}: {pipeline.name}")
                print(f"    Steps: {' -> '.join(s.bot_type for s in pipeline.steps)}")
                print(f"    Trigger: {pipeline.trigger}")
                print()

        elif args.command == "run":
            if not args.pipeline:
                print("Error: --pipeline required for 'run' command")
                return

            print(f"\n=== Running Pipeline: {args.pipeline} ===\n")

            # Prepare initial data
            initial_data = {}
            if args.project:
                initial_data["project_path"] = args.project
            if args.symbol:
                initial_data["symbol"] = args.symbol

            # Run pipeline
            run = await orchestrator.run_pipeline(
                pipeline_id=args.pipeline,
                trigger="manual",
                initial_data=initial_data,
            )

            print(f"\n=== Pipeline Complete ===")
            print(f"Status: {run.status}")
            print(f"Duration: {run.completed_at}")

            if run.context:
                print(f"\nBot Results:")
                for result in run.context.history:
                    status_icon = "✓" if result.status.value == "success" else "✗"
                    print(f"  {status_icon} {result.bot_type}: {result.status.value}")
                    if result.metrics:
                        for k, v in result.metrics.items():
                            print(f"      {k}: {v}")

        elif args.command == "status":
            print("\n=== Bot Army Status ===")
            print(f"Registered Bots: {len(orchestrator._bot_registry)}")
            for bot_type in orchestrator._bot_registry:
                print(f"  - {bot_type}")

            print(f"\nRegistered Pipelines: {len(orchestrator._pipelines)}")
            print(f"Recent Runs: {len(orchestrator._runs)}")

            # Show recent runs
            runs = orchestrator.list_runs()[:5]
            if runs:
                print("\nRecent Pipeline Runs:")
                for run in runs:
                    print(f"  - {run.run_id}: {run.pipeline_id} ({run.status})")

    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())
