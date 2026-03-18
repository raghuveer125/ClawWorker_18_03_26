import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))

from api import server
from trading.auto_trader import TradingMode


class DummyTrader:
    def __init__(self):
        self.start_calls = 0
        self.mode = None
        self.strategy_id = "test-strategy"
        self.recent_trade_calls = []
        self.status_payload = {
            "strategy_id": self.strategy_id,
            "positions": [{"symbol": "BANKNIFTY_58300PE", "status": "open"}],
            "open_positions": 1,
            "daily_pnl": 125.0,
        }
        self.recent_trades_payload = [
            {"trade_id": "paper-new", "mode": "paper"},
            {"trade_id": "paper-old", "mode": "paper"},
        ]

    def start(self):
        self.start_calls += 1

    def get_recent_trades(self, limit=100, mode=None):
        self.recent_trade_calls.append((limit, mode))
        trades = list(self.recent_trades_payload)
        if mode:
            trades = [trade for trade in trades if trade.get("mode") == mode]
        return trades[:limit]

    def get_status(self):
        return dict(self.status_payload)


class AutoTraderAutostartTests(unittest.TestCase):
    def test_autostart_starts_paper_trader_by_default(self):
        trader = DummyTrader()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTO_TRADER_AUTOSTART", None)
            with patch("trading.auto_trader.get_trading_mode_from_env", return_value=TradingMode.PAPER):
                with patch.object(server, "get_auto_trader", return_value=trader):
                    result = server.autostart_auto_trader_on_startup()

        self.assertTrue(result["attempted"])
        self.assertTrue(result["started"])
        self.assertEqual(result["mode"], "paper")
        self.assertEqual(result["strategy_id"], trader.strategy_id)
        self.assertEqual(trader.start_calls, 1)
        self.assertEqual(trader.mode, TradingMode.PAPER)

    def test_autostart_respects_disable_flag(self):
        trader = DummyTrader()
        with patch.dict(os.environ, {"AUTO_TRADER_AUTOSTART": "false"}, clear=False):
            with patch("trading.auto_trader.get_trading_mode_from_env", return_value=TradingMode.PAPER):
                with patch.object(server, "get_auto_trader", return_value=trader):
                    result = server.autostart_auto_trader_on_startup()

        self.assertFalse(result["attempted"])
        self.assertFalse(result["started"])
        self.assertEqual(result["mode"], "paper")
        self.assertEqual(trader.start_calls, 0)

    def test_autostart_skips_live_mode(self):
        trader = DummyTrader()
        with patch.dict(os.environ, {"AUTO_TRADER_AUTOSTART": "true"}, clear=False):
            with patch("trading.auto_trader.get_trading_mode_from_env", return_value=TradingMode.LIVE):
                with patch.object(server, "get_auto_trader", return_value=trader):
                    result = server.autostart_auto_trader_on_startup()

        self.assertFalse(result["attempted"])
        self.assertFalse(result["started"])
        self.assertEqual(result["mode"], "live")
        self.assertEqual(trader.start_calls, 0)

    def test_get_auto_trader_positions_returns_open_positions_only(self):
        trader = DummyTrader()
        with patch.object(server, "get_auto_trader", return_value=trader):
            result = asyncio.run(server.get_auto_trader_positions())

        self.assertEqual(result["strategy_id"], trader.strategy_id)
        self.assertEqual(result["positions"], trader.status_payload["positions"])
        self.assertEqual(result["open_count"], 1)
        self.assertEqual(result["daily_pnl"], 125.0)
        self.assertIn("timestamp", result)

    def test_get_auto_trader_trades_returns_stable_response_shape(self):
        trader = DummyTrader()
        with patch.object(server, "get_auto_trader", return_value=trader):
            result = asyncio.run(server.get_auto_trader_trades(limit=1, mode="paper"))

        self.assertEqual(trader.recent_trade_calls, [(1, "paper")])
        self.assertEqual(result["strategy_id"], trader.strategy_id)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["trades"], [{"trade_id": "paper-new", "mode": "paper"}])
        self.assertIn("timestamp", result)

    def test_get_auto_trader_trades_rejects_invalid_mode(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.get_auto_trader_trades(limit=10, mode="swing"))

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("paper", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
