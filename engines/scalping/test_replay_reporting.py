import sys
from pathlib import Path
import unittest


SCALPING_ROOT = Path(__file__).resolve().parent
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping.replay_reporting import ReplayDiagnosticsTracker


class ReplayReportingTests(unittest.TestCase):
    def test_replay_report_includes_strategy_tags_and_edge_discovery(self):
        tracker = ReplayDiagnosticsTracker()
        trades = [
            {
                "trade_id": "A1",
                "symbol": "BSE:SENSEX-INDEX",
                "strike": 76500,
                "option_type": "CE",
                "status": "closed",
                "outcome": "win",
                "realized_pnl": 240.0,
                "decision_packet": {
                    "setup_tag": "A+",
                    "rr_ratio": 1.55,
                    "timeframe_alignment": {
                        "1m_trend": "bullish",
                        "1m_aligned": True,
                        "3m_breakout": "bullish",
                        "3m_breakout_aligned": True,
                        "5m_trend": "bullish",
                        "5m_aligned": True,
                        "three_tf_aligned": True,
                    },
                    "micro_momentum": {"score": 0.82, "timing": "immediate", "aligned": True},
                    "entry_trigger": {"active": True, "types": ["volatility_burst"]},
                },
            },
            {
                "trade_id": "B1",
                "symbol": "BSE:SENSEX-INDEX",
                "strike": 76400,
                "option_type": "CE",
                "status": "closed",
                "outcome": "loss",
                "realized_pnl": -120.0,
                "decision_packet": {
                    "setup_tag": "B",
                    "rr_ratio": 0.85,
                    "timeframe_alignment": {
                        "1m_trend": "bullish",
                        "1m_aligned": True,
                        "3m_breakout": None,
                        "3m_breakout_aligned": False,
                        "5m_trend": "bullish",
                        "5m_aligned": True,
                        "three_tf_aligned": False,
                    },
                    "micro_momentum": {"score": 0.15, "timing": "confirm_window", "aligned": False},
                    "entry_trigger": {"active": False, "types": []},
                },
            },
        ]

        report = tracker.build_report(trades, simulated_pnl=120.0)

        self.assertIn("strategy_quality", report)
        self.assertIn("A+", report["strategy_quality"])
        self.assertEqual(report["strategy_quality"]["A+"]["trades"], 1)
        self.assertAlmostEqual(report["strategy_quality"]["A+"]["expectancy"], 240.0, places=2)
        self.assertIn("edge_discovery", report)
        self.assertEqual(report["edge_discovery"]["rr_buckets"]["1.5+"]["trades"], 1)
        self.assertEqual(report["edge_discovery"]["rr_buckets"]["0.8"]["trades"], 1)
        self.assertEqual(len(report["edge_discovery"]["trade_feature_log"]), 2)


if __name__ == "__main__":
    unittest.main()
