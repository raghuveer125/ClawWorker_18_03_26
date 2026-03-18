import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))

from trading.auto_trader import AutoTrader, RiskConfig, TradingMode


class FakeEnsemble:
    def analyze(self, index, market_data):
        return None

    def execute_trade(self, decision, market_data):
        return None

    def close_trade(self, index, exit_price, outcome, pnl, exit_reason):
        return None

    def reset_daily(self):
        return None


class StrategyIsolationTests(unittest.TestCase):
    def test_duplicate_strategy_id_is_rejected(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        runtime_root = Path(temp_dir.name) / "runtime"

        with patch.dict(os.environ, {"PAPER_TRADING_RUNTIME_ROOT": str(runtime_root)}, clear=False):
            trader_one = AutoTrader(
                ensemble=FakeEnsemble(),
                risk_config=RiskConfig(),
                mode=TradingMode.PAPER,
                data_dir=str(Path(temp_dir.name) / "data_one"),
                strategy_id="isolated-alpha",
            )
            trader_two = AutoTrader(
                ensemble=FakeEnsemble(),
                risk_config=RiskConfig(),
                mode=TradingMode.PAPER,
                data_dir=str(Path(temp_dir.name) / "data_two"),
                strategy_id="isolated-alpha",
            )

            with patch.object(AutoTrader, "_trading_loop", autospec=True, return_value=None):
                trader_one.start()
                self.addCleanup(trader_one.stop)

                with self.assertRaises(RuntimeError) as ctx:
                    trader_two.start()

        self.assertIn("isolated-alpha", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
