import unittest
from pathlib import Path

from livebench.tools.closed_market_health import find_reference_csv, run_historical_bot_replay


class ClosedMarketHealthTests(unittest.TestCase):
    def test_reference_csv_exists(self):
        csv_path = find_reference_csv()
        self.assertTrue(csv_path.exists())
        self.assertEqual(csv_path.suffix, ".csv")

    def test_historical_bot_replay_returns_expected_structure(self):
        csv_path = find_reference_csv()
        report = run_historical_bot_replay(csv_path)

        self.assertEqual(Path(report["reference_csv"]), csv_path)
        self.assertGreater(report["total_rows"], 100)
        self.assertIn("TrendFollower", report["bot_signal_counts"])
        self.assertIn("ICTSniper", report["bot_signal_counts"])
        self.assertGreaterEqual(report["bot_signal_counts"]["TrendFollower"], 1)
        self.assertGreaterEqual(report["bot_signal_counts"]["ICTSniper"], 1)


if __name__ == "__main__":
    unittest.main()
