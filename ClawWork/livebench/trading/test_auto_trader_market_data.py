import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))

from trading.auto_trader import AutoTrader, RiskConfig


class FakeDecision:
    def __init__(
        self,
        action="BUY_PE",
        strike=78800,
        confidence=72.0,
        consensus_level=60.0,
        entry=240.0,
        stop_loss=192.0,
        target=312.0,
    ):
        self.action = action
        self.strike = strike
        self.confidence = confidence
        self.consensus_level = consensus_level
        self.entry = entry
        self.stop_loss = stop_loss
        self.target = target
        self.contributing_bots = ["TrendFollower", "MomentumScalper"]
        self.reasoning = "Test signal"


class FakeEnsemble:
    def __init__(self, decision=None):
        self.decision = decision or FakeDecision()

    def analyze(self, index, market_data):
        return self.decision

    def execute_trade(self, decision, market_data):
        return None

    def close_trade(self, index, exit_price, outcome, pnl, exit_reason):
        return None

    def reset_daily(self):
        return None


class AutoTraderMarketDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base_dir = Path(self.temp_dir.name)
        self.data_dir = self.base_dir / "auto_trader"
        self.screener_dir = self.base_dir / "fyers"
        self.trader = AutoTrader(
            ensemble=FakeEnsemble(),
            risk_config=RiskConfig(max_daily_profit=10000.0),
            data_dir=str(self.data_dir),
        )
        self.trader.screener_dir = self.screener_dir
        self.trader.screener_max_age_seconds = 60
        self.trader.screener_refresh_cooldown_seconds = 1
        self.trader.market_data_status["healthy"] = True
        self.trader.market_data_status["available"] = True
        self.trader.market_data_status["message"] = "OK"

    def _payload(self):
        return {
            "success": True,
            "market_bias": "BEARISH",
            "index_recommendations": [
                {
                    "index": "SENSEX",
                    "ltp": 78918.9,
                    "change_pct": -1.37,
                    "signal": "BEARISH",
                    "option_side": "PE",
                    "atm_strike": 78900,
                    "preferred_strike": 78800,
                    "strike_step": 100,
                    "confidence": 72,
                    "reason": "Fresh test payload",
                    "candidate_strikes": [{"label": "ATM", "strike": 78900}],
                }
            ],
            "index_symbols": {"SENSEX": []},
            "results": [],
        }

    def _trusted_trade_row(
        self,
        *,
        trade_id: str,
        probability: float,
        consensus: float,
        pnl: float = 250.0,
        pnl_pct: float = 5.0,
    ):
        return {
            "trade_id": trade_id,
            "timestamp": "2026-03-06T12:38:25.842759",
            "symbol": "BANKNIFTY_58300PE",
            "index": "BANKNIFTY",
            "option_type": "PE",
            "strike": 58300,
            "entry_price": 582.53,
            "exit_price": 612.53,
            "quantity": 30,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "outcome": "WIN",
            "exit_reason": "TARGET",
            "duration_minutes": 4,
            "market_bias": "",
            "bot_signals": {"confidence": probability, "consensus": consensus},
            "probability": probability,
            "conviction": "HIGH",
            "index_change_pct": 0,
            "vix": 0,
            "pcr": 0,
            "was_counter_trend": False,
            "was_gap_trade": False,
            "was_overbought": False,
            "was_oversold": False,
            "mode": "paper",
        }

    def test_fetch_refreshes_when_cached_snapshot_is_stale(self):
        self.screener_dir.mkdir(parents=True, exist_ok=True)
        stale_file = self.screener_dir / "screener_20260306_160242.json"
        stale_file.write_text('{"success": true, "market_bias": "NEUTRAL", "index_recommendations": []}')
        stale_ts = time.time() - 3600
        os.utime(stale_file, (stale_ts, stale_ts))

        with patch("trading.screener.run_screener", return_value=self._payload()):
            market_data = self.trader._fetch_screener_data()

        self.assertIsNotNone(market_data)
        self.assertIn("SENSEX", market_data)
        self.assertEqual(self.trader.market_data_status["source"], "live_refresh")
        self.assertTrue(self.trader.market_data_status["healthy"])

    def test_recent_exit_cooldown_is_enforced_after_close(self):
        market_data = {
            "ltp": 78918.9,
            "change_pct": -1.0,
            "signal": "BEARISH",
            "option_side": "PE",
            "stocks": [],
        }
        signal = {
            "decision": self.trader.ensemble.decision,
            "index": "SENSEX",
            "market_data": market_data,
        }

        position = self.trader.execute_trade(signal)
        self.assertIsNotNone(position)
        self.trader.close_position(position, exit_price=position.target, exit_reason="TARGET")

        with patch.object(self.trader, "check_can_trade", return_value=(True, "OK")):
            result = self.trader.process_signal("SENSEX", market_data)
        self.assertEqual(result["action"], "SKIP")
        self.assertIn("Cooldown", result["reason"])

    def test_paper_price_simulation_does_not_fall_back_to_index_spot(self):
        market_data = {
            "ltp": 78918.9,
            "change_pct": -1.2,
            "signal": "BEARISH",
            "option_side": "PE",
            "stocks": [],
        }
        signal = {
            "decision": self.trader.ensemble.decision,
            "index": "SENSEX",
            "market_data": market_data,
        }
        position = self.trader.execute_trade(signal)
        self.assertIsNotNone(position)

        prices = self.trader._get_current_prices({"SENSEX": market_data})
        self.assertIn(position.symbol, prices)
        self.assertNotIn(position.index, prices)
        self.assertLess(prices[position.symbol], 1000)

    def test_startup_quarantines_legacy_trade_rows_and_rebuilds_learning(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        base_dir = Path(temp_dir.name)
        data_dir = base_dir / "auto_trader"
        data_dir.mkdir(parents=True, exist_ok=True)

        good_row = {
            "trade_id": "BANKNIFTY_PE_123825840846",
            "timestamp": "2026-03-06T12:38:25.842759",
            "symbol": "BANKNIFTY_58300PE",
            "index": "BANKNIFTY",
            "option_type": "PE",
            "strike": 58300,
            "entry_price": 582.53,
            "exit_price": 602.33602,
            "quantity": 30,
            "pnl": 594.1805999999997,
            "pnl_pct": 3.399999999999998,
            "outcome": "WIN",
            "exit_reason": "TARGET",
            "duration_minutes": 0,
            "market_bias": "",
            "bot_signals": {"confidence": 59.1},
            "probability": 59.1,
            "conviction": "HIGH",
            "index_change_pct": 0,
            "vix": 0,
            "pcr": 0,
            "was_counter_trend": False,
            "was_gap_trade": False,
            "was_overbought": False,
            "was_oversold": False,
            "mode": "paper",
        }
        legacy_row = {
            **good_row,
            "trade_id": "BANKNIFTY_PE_133647",
            "timestamp": "2026-03-02T13:41:12.857754",
            "mode": None,
        }
        corrupt_row = {
            **good_row,
            "trade_id": "BANKNIFTY_PE_134122908589",
            "timestamp": "2026-03-02T13:52:14.720030",
            "exit_price": 59500.45,
            "pnl": 1765046.4932499998,
            "pnl_pct": 9267.936707864284,
        }

        trades_file = data_dir / "trades_log.jsonl"
        trades_file.write_text(
            "\n".join(json.dumps(row) for row in [legacy_row, corrupt_row, good_row]) + "\n",
            encoding="utf-8",
        )

        trader = AutoTrader(
            ensemble=FakeEnsemble(),
            risk_config=RiskConfig(),
            data_dir=str(data_dir),
        )

        cleaned_rows = [
            json.loads(line)
            for line in trades_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(cleaned_rows), 1)
        self.assertEqual(cleaned_rows[0]["trade_id"], good_row["trade_id"])

        quarantine_rows = [
            json.loads(line)
            for line in (data_dir / "trades_log_quarantine.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(quarantine_rows), 2)
        self.assertEqual(
            {row["reason"] for row in quarantine_rows},
            {"missing_or_invalid_mode", "implausible_exit_price"},
        )

        insights = json.loads((data_dir / "learning_insights.json").read_text(encoding="utf-8"))
        self.assertEqual(insights["total_trades"], 1)
        self.assertEqual(insights["wins"], 1)
        self.assertAlmostEqual(insights["total_pnl_paper"], good_row["pnl"])

    def test_get_recent_trades_returns_newest_first_and_filters_mode(self):
        rows = [
            self._trusted_trade_row(trade_id="paper-old", probability=61.0, consensus=58.0),
            self._trusted_trade_row(trade_id="live-middle", probability=72.0, consensus=66.0),
            self._trusted_trade_row(trade_id="paper-new", probability=81.0, consensus=73.0),
        ]
        rows[1]["mode"] = "live"

        self.trader.trades_log_file.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )

        recent_all = self.trader.get_recent_trades(limit=2)
        recent_paper = self.trader.get_recent_trades(limit=10, mode="paper")

        self.assertEqual([trade["trade_id"] for trade in recent_all], ["paper-new", "live-middle"])
        self.assertEqual([trade["trade_id"] for trade in recent_paper], ["paper-new", "paper-old"])

    def test_get_recent_trades_ignores_invalid_rows(self):
        valid_row = self._trusted_trade_row(trade_id="paper-valid", probability=78.0, consensus=71.0)
        self.trader.trades_log_file.write_text(
            "\n".join(
                [
                    "{not-json}",
                    json.dumps(valid_row),
                    "",
                ]
            ),
            encoding="utf-8",
        )

        trades = self.trader.get_recent_trades(limit=5, mode="paper")

        self.assertEqual(trades, [valid_row])

    def test_startup_cleans_legacy_execution_quality_store(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        base_dir = Path(temp_dir.name)
        data_dir = base_dir / "auto_trader"
        legacy_dir = data_dir / "execution_quality"
        canonical_dir = base_dir / "execution_quality"

        (legacy_dir / "gates").mkdir(parents=True, exist_ok=True)
        (canonical_dir / "gates").mkdir(parents=True, exist_ok=True)

        (legacy_dir / "config.json").write_text('{"source":"legacy"}', encoding="utf-8")
        (legacy_dir / "gates" / "paper_validation_result.json").write_text(
            '{"status":"legacy"}',
            encoding="utf-8",
        )
        (legacy_dir / "gates" / "legacy_only.json").write_text(
            '{"status":"migrate"}',
            encoding="utf-8",
        )

        (canonical_dir / "config.json").write_text('{"source":"canonical"}', encoding="utf-8")
        (canonical_dir / "gates" / "paper_validation_result.json").write_text(
            '{"status":"canonical"}',
            encoding="utf-8",
        )

        trader = AutoTrader(
            ensemble=FakeEnsemble(),
            risk_config=RiskConfig(),
            data_dir=str(data_dir),
        )

        self.assertFalse(legacy_dir.exists())
        self.assertEqual((canonical_dir / "config.json").read_text(encoding="utf-8"), '{"source":"canonical"}')
        self.assertEqual(
            (canonical_dir / "gates" / "paper_validation_result.json").read_text(encoding="utf-8"),
            '{"status":"canonical"}',
        )
        self.assertEqual(
            (canonical_dir / "gates" / "legacy_only.json").read_text(encoding="utf-8"),
            '{"status":"migrate"}',
        )
        self.assertTrue(trader.execution_quality_status["cleanup_performed"])
        self.assertIn("config.json", trader.execution_quality_status["discarded_files"])
        self.assertIn("gates/legacy_only.json", trader.execution_quality_status["migrated_files"])

    def test_trusted_learning_thresholds_are_used_for_signal_filtering(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        base_dir = Path(temp_dir.name)
        data_dir = base_dir / "auto_trader"
        data_dir.mkdir(parents=True, exist_ok=True)

        trades = [
            self._trusted_trade_row(
                trade_id=f"BANKNIFTY_PE_{idx}",
                probability=78.0 + idx,
                consensus=68.0 + idx,
            )
            for idx in range(5)
        ]
        (data_dir / "trades_log.jsonl").write_text(
            "\n".join(json.dumps(row) for row in trades) + "\n",
            encoding="utf-8",
        )

        trader = AutoTrader(
            ensemble=FakeEnsemble(decision=FakeDecision(confidence=72.0, consensus_level=65.0)),
            risk_config=RiskConfig(min_probability=55, min_consensus=0.33),
            data_dir=str(data_dir),
        )
        trader.learning_adaptation_min_trades = 5
        trader.learning_adaptation_min_wins = 3
        trader.market_data_status["healthy"] = True
        trader.market_data_status["available"] = True

        thresholds = trader.get_effective_thresholds()
        self.assertTrue(thresholds["adaptive_applied"])
        self.assertEqual(thresholds["trusted_trades"], 5)
        self.assertEqual(thresholds["trusted_wins"], 5)
        self.assertGreaterEqual(thresholds["min_probability"], 78)
        self.assertGreaterEqual(thresholds["min_consensus_pct"], 68.0)

        market_data = {
            "ltp": 78918.9,
            "change_pct": -1.0,
            "signal": "BEARISH",
            "option_side": "PE",
            "stocks": [],
        }
        with patch.object(trader, "check_can_trade", return_value=(True, "OK")):
            result = trader.process_signal("SENSEX", market_data)

        self.assertEqual(result["action"], "SKIP")
        self.assertIn("adaptive", result["reason"])
        self.assertTrue(
            "Low confidence" in result["reason"] or "Low consensus" in result["reason"]
        )


if __name__ == "__main__":
    unittest.main()
