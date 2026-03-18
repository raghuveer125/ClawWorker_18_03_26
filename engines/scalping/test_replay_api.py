import sys
from pathlib import Path
import unittest


SCALPING_ROOT = Path(__file__).resolve().parent
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping import api
from scalping.base import BotContext


class _FakeReplayAdapter:
    def __init__(self, total_batches: int = 2):
        self._total_batches = total_batches
        self._cursor = 0

    def total_batches(self) -> int:
        return self._total_batches

    def has_next(self) -> bool:
        return self._cursor < self._total_batches

    def has_previous(self) -> bool:
        return self._cursor > 0

    def current_index(self) -> int:
        return self._cursor


class _FakeEngine:
    REPLAY = "REPLAY"

    def __init__(self):
        self.cycle_count = 0
        self.replay_interval_ms = 1
        self.replay_adapter = _FakeReplayAdapter(total_batches=2)
        self.context = BotContext(data={"capital_state": {"total_pnl": 0}, "executed_trades": []})
        self.finished_report = None
        self.position_manager = type(
            "FakePositionManager",
            (),
            {
                "__init__": lambda pm, context: setattr(pm, "context", context) or setattr(pm, "flatten_calls", []),
                "flatten_open_positions": lambda pm, context, reason="Replay completed": pm.flatten_calls.append(reason),
            },
        )(self.context)

    def start_replay(self, csv_path: str) -> None:
        self.started_replay = csv_path

    async def run_cycle(self):
        self.cycle_count += 1
        self.replay_adapter._cursor += 1
        self.context.data["cycle_timestamp"] = f"cycle-{self.cycle_count}"
        return {"status": "ok"}

    def finish_replay(self, report):
        self.finished_report = report


class ReplayApiTests(unittest.IsolatedAsyncioTestCase):
    def test_replay_cycles_per_chunk_tracks_speed_presets(self):
        self.assertEqual(api._replay_cycles_per_chunk(1.0), 1)
        self.assertEqual(api._replay_cycles_per_chunk(2.0), 2)
        self.assertEqual(api._replay_cycles_per_chunk(4.0), 4)
        self.assertEqual(api._replay_cycles_per_chunk(8.0), 8)

    async def test_run_replay_job_completes_when_forward_replay_reaches_end(self):
        old_engine = api._engine_instance
        old_broadcast = api.broadcast_update
        state = api.get_state()
        previous_state = {
            "replay_active": state.replay_active,
            "replay_paused": state.replay_paused,
            "replay_progress_pct": state.replay_progress_pct,
            "replay_dataset": state.replay_dataset,
            "replay_result": dict(state.replay_result),
            "replay_direction": state.replay_direction,
            "replay_position": state.replay_position,
            "replay_total_batches": state.replay_total_batches,
            "mode": state.mode,
        }

        fake_engine = _FakeEngine()

        async def _noop_broadcast(update_type, data):
            return None

        api._engine_instance = fake_engine
        api.broadcast_update = _noop_broadcast

        try:
            report = await api._run_replay_job("/tmp/demo.csv", "demo.csv")
        finally:
            api._engine_instance = old_engine
            api.broadcast_update = old_broadcast
            state.replay_active = previous_state["replay_active"]
            state.replay_paused = previous_state["replay_paused"]
            state.replay_progress_pct = previous_state["replay_progress_pct"]
            state.replay_dataset = previous_state["replay_dataset"]
            state.replay_result = previous_state["replay_result"]
            state.replay_direction = previous_state["replay_direction"]
            state.replay_position = previous_state["replay_position"]
            state.replay_total_batches = previous_state["replay_total_batches"]
            state.mode = previous_state["mode"]

        self.assertEqual(report["dataset"], "demo.csv")
        self.assertEqual(report["total_cycles"], 2)
        self.assertFalse(state.replay_active)
        self.assertFalse(state.replay_paused)
        self.assertEqual(fake_engine.finished_report["dataset"], "demo.csv")
        self.assertEqual(fake_engine.finished_report["total_cycles"], 2)
        self.assertEqual(fake_engine.position_manager.flatten_calls, ["Replay completed"])


if __name__ == "__main__":
    unittest.main()
