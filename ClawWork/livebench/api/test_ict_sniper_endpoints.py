import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))

from api import server
from bots.base import BotSignal, OptionType, SignalType


class FakeICTBot:
    def __init__(self):
        self.name = "ICTSniper"
        self.description = "Test ICT bot"
        self.performance = SimpleNamespace(
            total_signals=2,
            total_trades=1,
            wins=1,
            losses=0,
            win_rate=100.0,
            total_pnl=250.0,
            weight=1.2,
        )
        self.config = SimpleNamespace(
            swing_lookback=9,
            mss_swing_len=2,
            max_bars_after_sweep=10,
            vol_multiplier=1.2,
            displacement_multiplier=1.3,
            rr_ratio=2.0,
            atr_sl_buffer=0.5,
            max_fvg_size=3.0,
            entry_type="Both",
            require_displacement=True,
            require_volume_spike=True,
        )
        self.signal_history = [
            {
                "timestamp": "2026-03-09T14:55:00",
                "signal_type": "BUY",
                "option_type": "CE",
                "confidence": 85.0,
                "metadata": {
                    "signal_1m": True,
                    "signal_5m": False,
                    "signal_15m": False,
                    "confluence": 1,
                    "entry_sources": ["FVG", "OB"],
                },
            }
        ]
        self.learn_calls = []

    def get_multi_timeframe_state(self):
        return {
            "1m": {"bullish_setup_active": True, "bearish_setup_active": False},
            "5m": {"bullish_setup_active": False, "bearish_setup_active": False},
            "15m": {"bullish_setup_active": False, "bearish_setup_active": False},
        }

    def analyze(self, index, market_data):
        return BotSignal(
            bot_name="ICTSniper",
            index=index,
            signal_type=SignalType.BUY,
            option_type=OptionType.CE,
            confidence=85.0,
            entry=float(market_data.get("close", 0.0) or 0.0),
            stop_loss=84.7,
            target=86.3,
            reasoning="Bullish LQ Grab + MSS + FVG/OB",
            factors={"entry_sources": ["FVG", "OB"], "timeframe": "1m"},
        )

    def learn(self, trade_record):
        self.learn_calls.append(trade_record)


class FakeEnsemble:
    def __init__(self, bot):
        self.bot_map = {"ICTSniper": bot}


class ICTSniperEndpointTests(unittest.TestCase):
    def setUp(self):
        self.bot = FakeICTBot()
        self.client = TestClient(server.app)

    def test_status_payload_contains_recent_signals_and_mtf_state(self):
        with patch.object(server, "get_ensemble", return_value=FakeEnsemble(self.bot)):
            response = self.client.get("/api/bots/ict-sniper/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("multi_timeframe_state", payload)
        self.assertIn("warmup_state", payload)
        self.assertEqual(payload["recent_signals"][0]["metadata"]["confluence"], 1)
        self.assertTrue(payload["setup_state"]["bullish_setup_active"])

    def test_direct_analyze_endpoint_returns_ict_decision(self):
        with patch.object(server, "get_ensemble", return_value=FakeEnsemble(self.bot)), patch.object(server, "_warm_ict_sniper_from_history", return_value=None):
            response = self.client.post(
                "/api/bots/ict-sniper/analyze",
                json={"index": "SENSEX", "market_data": {"close": 85.24}},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["has_decision"])
        self.assertEqual(payload["decision"]["action"], "BUY_CE")
        self.assertEqual(payload["decision"]["analysis"]["entry_sources"], ["FVG", "OB"])

    def test_build_ict_warmup_candles_excludes_current_bar(self):
        candles = [
            [1000, 1.0, 2.0, 0.5, 1.5, 10],
            [1060, 2.0, 3.0, 1.5, 2.5, 20],
            [1120, 3.0, 4.0, 2.5, 3.5, 30],
        ]

        prepared = server._build_ict_warmup_candles(candles, {"bar_index": 1120 // 60}, limit=10)

        self.assertEqual(len(prepared), 2)
        self.assertEqual(prepared[-1]["close"], 2.5)


if __name__ == "__main__":
    unittest.main()
