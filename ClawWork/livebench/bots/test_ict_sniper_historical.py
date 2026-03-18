import csv
import sys
import tempfile
import unittest
from pathlib import Path


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = LIVEBENCH_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from livebench.bots.base import SharedMemory
from livebench.bots.ict_sniper import ICTSniperBot


class ICTSniperHistoricalReplayTests(unittest.TestCase):
    def _load_rows(self):
        ohlcv_path = (
            PROJECT_ROOT.parent
            / "fyersN7"
            / "fyers-2026-03-05"
            / "postmortem"
            / "2026-03-09"
            / "SENSEX"
            / "sensex_1m_ohlcv_2026-03-09.csv"
        )
        self.assertTrue(ohlcv_path.exists(), f"Missing historical file: {ohlcv_path}")
        with ohlcv_path.open() as handle:
            return list(csv.DictReader(handle))

    def test_sensex_historical_replay_generates_signal_and_metadata(self):
        rows = self._load_rows()

        with tempfile.TemporaryDirectory() as temp_dir:
            bot = ICTSniperBot(shared_memory=SharedMemory(temp_dir))
            signals = []
            for bar_index, row in enumerate(rows):
                signal = bot.analyze(
                    "SENSEX",
                    {
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "bar_index": bar_index,
                    },
                )
                if signal:
                    signals.append(signal)

            self.assertGreaterEqual(len(signals), 1)
            self.assertGreaterEqual(len(bot.recent_signals), 1)
            self.assertGreaterEqual(len(bot.signal_history), 1)

            latest = bot.signal_history[-1]
            metadata = latest.get("metadata", {})
            self.assertIn("signal_1m", metadata)
            self.assertIn("entry_sources", metadata)
            self.assertTrue(metadata["signal_1m"] or metadata["signal_5m"] or metadata["signal_15m"])
            self.assertTrue(latest.get("option_type") in {"CE", "PE"})

    def test_warmup_seeds_state_without_emitting_historical_signals(self):
        rows = self._load_rows()
        warmup_rows = []
        for bar_index, row in enumerate(rows[:120]):
            warmup_rows.append({
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "bar_index": bar_index,
            })

        with tempfile.TemporaryDirectory() as temp_dir:
            bot = ICTSniperBot(shared_memory=SharedMemory(temp_dir))
            status = bot.warmup("SENSEX", warmup_rows, session_key="2026-03-09")

            self.assertEqual(status["bars_loaded"], len(warmup_rows))
            self.assertTrue(bot.warmup_session_matches("SENSEX", "2026-03-09"))
            self.assertEqual(bot.performance.total_signals, 0)
            self.assertEqual(len(bot.signal_history), 0)
            self.assertGreater(len(bot.tf_states["1m"].recent_volumes), 0)

    def test_state_is_isolated_per_index(self):
        rows = self._load_rows()
        with tempfile.TemporaryDirectory() as temp_dir:
            bot = ICTSniperBot(shared_memory=SharedMemory(temp_dir))
            for bar_index, row in enumerate(rows[:40]):
                candle = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "bar_index": bar_index,
                }
                bot.analyze("SENSEX", candle, emit_signals=False)

            bot.analyze(
                "NIFTY50",
                {
                    "open": 22000.0,
                    "high": 22010.0,
                    "low": 21990.0,
                    "close": 22005.0,
                    "volume": 1000.0,
                    "bar_index": 1,
                },
                emit_signals=False,
            )

            sensex_state = bot._get_tf_states("SENSEX")["1m"]
            nifty_state = bot._get_tf_states("NIFTY50")["1m"]
            self.assertNotEqual(list(sensex_state.recent_closes), list(nifty_state.recent_closes))
            self.assertGreater(len(sensex_state.recent_closes), len(nifty_state.recent_closes))


if __name__ == "__main__":
    unittest.main()
