import asyncio
import sys
from pathlib import Path
import unittest


SCALPING_ROOT = Path(__file__).resolve().parent
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping import api
from scalping.base import BotResult, BotStatus
from scalping.config import ScalpingConfig
from scalping.engine import ScalpingEngine


class _StubAgent:
    def __init__(self, bot_type: str, status: BotStatus = BotStatus.SUCCESS, output=None, metrics=None, on_run=None):
        self.bot_type = bot_type
        self.status = status
        self.output = output or {}
        self.metrics = metrics or {}
        self.on_run = on_run
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        if self.on_run is not None:
            self.on_run(context)
        return BotResult(
            bot_id=f"{self.bot_type}_stub",
            bot_type=self.bot_type,
            status=self.status,
            output=dict(self.output),
            metrics=dict(self.metrics),
        )


class _BlockedKillSwitch(_StubAgent):
    def __init__(self):
        super().__init__(
            "kill_switch",
            status=BotStatus.BLOCKED,
            output={"reason": "volatility_shock", "triggered_at": "2026-03-18T13:38:07"},
        )

    def get_state(self):
        return {
            "active": True,
            "reason": "volatility_shock",
            "triggered_at": "2026-03-18T13:38:07",
            "auto_reset_at": "2026-03-18T13:53:07",
        }


class KillSwitchMonitoringTests(unittest.IsolatedAsyncioTestCase):
    async def test_kill_switch_keeps_monitoring_pipeline_alive(self):
        api.init_agents()
        state = api.get_state()
        previous_state = {
            "cycle_count": state.cycle_count,
            "last_cycle_time": state.last_cycle_time,
            "last_cycle_duration": state.last_cycle_duration,
            "kill_switch_active": state.kill_switch_active,
            "kill_switch_reason": state.kill_switch_reason,
            "kill_switch_triggered_at": state.kill_switch_triggered_at,
            "mode": state.mode,
            "running": state.running,
        }

        engine = ScalpingEngine(config=ScalpingConfig(), dry_run=True)
        engine.resolve_mode = lambda: engine.LIVE_PAPER
        engine._record_flow = lambda *args, **kwargs: None
        engine._validate_stage_inputs = lambda *args, **kwargs: None
        engine._print_cycle_summary = lambda *args, **kwargs: None

        async def _noop_async(*args, **kwargs):
            return None

        engine._emit_market_event = _noop_async
        engine._publish_execution_snapshot = _noop_async

        def _populate_market_data(context):
            context.data["spot_data"] = {"NSE:NIFTY50-INDEX": {"ltp": 24500.0, "timestamp": "2026-03-18T13:44:00"}}

        try:
            engine.kill_switch = _BlockedKillSwitch()
            engine.data_feed = _StubAgent("data_feed", metrics={"symbols_fetched": 1}, on_run=_populate_market_data)
            engine.option_chain = _StubAgent("option_chain")
            engine.futures = _StubAgent("futures")
            engine.market_regime = _StubAgent("market_regime")
            engine.structure = _StubAgent("structure")
            engine.momentum = _StubAgent("momentum")
            engine.trap_detector = _StubAgent("trap_detector")
            engine.volatility_surface = _StubAgent("volatility_surface")
            engine.dealer_pressure = _StubAgent("dealer_pressure")
            engine.strike_selector = _StubAgent("strike_selector")
            engine.signal_quality = _StubAgent("signal_quality")
            engine.liquidity_monitor = _StubAgent("liquidity_monitor")
            engine.risk_guardian = _StubAgent("risk_guardian")
            engine.correlation_guard = _StubAgent("correlation_guard")
            engine.meta_allocator = _StubAgent("meta_allocator")
            engine.entry = _StubAgent("entry")
            engine.exit = _StubAgent("exit")
            engine.position_manager = _StubAgent("position_manager")
            engine.quant_learner = _StubAgent("quant_learner")
            engine.strategy_optimizer = _StubAgent("strategy_optimizer")
            engine.exit_optimizer = _StubAgent("exit_optimizer")

            await engine.start()
            results = await engine.run_cycle()
        finally:
            await engine.stop()

        try:
            self.assertEqual(results["kill_switch"].status, BotStatus.BLOCKED)
            self.assertIn("data_feed", results)
            self.assertEqual(engine.data_feed.calls, 1)
            self.assertEqual(engine.entry.calls, 0)
            self.assertTrue(engine.context.data["trade_disabled"])
            self.assertEqual(engine.context.data["trade_disabled_reason"], "kill_switch:volatility_shock")
            self.assertTrue(state.kill_switch_active)
            self.assertEqual(state.kill_switch_reason, "volatility_shock")
            self.assertIsNotNone(state.last_cycle_time)
        finally:
            state.cycle_count = previous_state["cycle_count"]
            state.last_cycle_time = previous_state["last_cycle_time"]
            state.last_cycle_duration = previous_state["last_cycle_duration"]
            state.kill_switch_active = previous_state["kill_switch_active"]
            state.kill_switch_reason = previous_state["kill_switch_reason"]
            state.kill_switch_triggered_at = previous_state["kill_switch_triggered_at"]
            state.mode = previous_state["mode"]
            state.running = previous_state["running"]


if __name__ == "__main__":
    unittest.main()
