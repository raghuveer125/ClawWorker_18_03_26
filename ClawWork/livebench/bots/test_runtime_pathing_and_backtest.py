import builtins
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))

from bots.ai_optimizer import AIOptimizer, DEFAULT_CONFIG_DIR, DEFAULT_LOG_DIR
from bots.base import SharedMemory
from bots.ensemble import EnsembleCoordinator
from backtesting import backtest as backtest_module


class RuntimePathingAndBacktestTests(unittest.TestCase):
    def test_ai_optimizer_defaults_to_current_repo_paths(self):
        optimizer = AIOptimizer()
        self.assertEqual(optimizer.log_dir, DEFAULT_LOG_DIR)
        self.assertEqual(optimizer.config_dir, DEFAULT_CONFIG_DIR)

    def test_ai_optimizer_persists_supported_runtime_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            optimizer = AIOptimizer(config_dir=str(config_dir))

            applied = optimizer.apply_suggestion(
                type("Suggestion", (), {
                    "parameter": "min_signal_strength",
                    "suggested_value": 35,
                    "reason": "test",
                })()
            )

            self.assertTrue(applied)
            with open(optimizer.runtime_overrides_file, "r") as f:
                persisted = json.load(f)
            self.assertEqual(persisted["min_signal_strength"], 35.0)

    def test_ai_optimizer_module_loads_without_dotenv(self):
        module_path = LIVEBENCH_ROOT / "bots" / "ai_optimizer.py"
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "dotenv":
                raise ImportError("No module named 'dotenv'")
            return real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = fake_import
        try:
            namespace = runpy.run_path(str(module_path))
        finally:
            builtins.__import__ = real_import

        self.assertIsNone(namespace["load_dotenv"])

    def test_shared_memory_defaults_to_current_repo_path(self):
        memory = SharedMemory()
        self.assertEqual(memory.data_dir, SharedMemory.DEFAULT_DATA_DIR)

    def test_backtester_imports_in_top_level_runtime_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backtester = backtest_module.Backtester(data_dir=tmpdir)
            self.assertEqual(backtester.data_dir, Path(tmpdir))
            self.assertEqual(backtester.memory.data_dir, Path(tmpdir) / "backtest_memory")

    def test_ensemble_backtest_validation_runs_with_top_level_backtester(self):
        original_backtester = backtest_module.Backtester

        class FakeBacktester:
            def __init__(self, *args, **kwargs):
                self.bot_trades = {"TrendFollower": [{"pnl": 150.0}, {"pnl": -50.0}]}

            def run_backtest(self, index, days, resolution):
                return {"index": index, "days": days, "resolution": resolution}

        backtest_module.Backtester = FakeBacktester
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                memory = SharedMemory(data_dir=str(Path(tmpdir) / "bots"))
                ensemble = EnsembleCoordinator(memory)
                ensemble.drift_active = False
                ensemble.trades_since_backtest["TrendFollower"] = ensemble.config.backtest_every_n_trades
                ensemble._run_backtest_validation("TrendFollower", "SENSEX")
                self.assertIn("TrendFollower", ensemble.last_backtest_results)
                self.assertEqual(ensemble.trades_since_backtest["TrendFollower"], 0)
        finally:
            backtest_module.Backtester = original_backtester

    def test_ensemble_loads_runtime_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "bots"
            data_dir.mkdir(parents=True, exist_ok=True)
            overrides_file = data_dir / "ensemble_runtime_overrides.json"
            with open(overrides_file, "w") as f:
                json.dump({
                    "min_signal_strength": 35,
                    "min_confidence": 52,
                    "high_conviction_threshold": 65,
                    "mtf_mode": "permissive",
                }, f)

            memory = SharedMemory(data_dir=str(data_dir))
            ensemble = EnsembleCoordinator(memory)
            self.assertEqual(ensemble.config.min_signal_strength, 35.0)
            self.assertEqual(ensemble.config.min_confidence, 52.0)
            self.assertEqual(ensemble.config.high_conviction_threshold, 65.0)
            self.assertEqual(ensemble.config.mtf_mode, "permissive")


if __name__ == "__main__":
    unittest.main()
