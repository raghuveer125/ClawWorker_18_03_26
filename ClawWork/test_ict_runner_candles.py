import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from paper_trading_runner import FyersDataProvider


class IctRunnerCandleTests(unittest.TestCase):
    def test_latest_completed_candle_excludes_current_open_minute(self):
        candles = [
            [1000, 1, 2, 0.5, 1.5, 10],
            [1060, 2, 3, 1.5, 2.5, 20],
            [1120, 3, 4, 2.5, 3.5, 30],
        ]

        with patch("paper_trading_runner.time.time", return_value=1135):
            latest = FyersDataProvider._latest_completed_candle(candles)

        self.assertEqual(latest, candles[1])

    def test_parse_history_candles_handles_wrapped_payload(self):
        payload = {"data": {"candles": [[1000, 1, 2, 0.5, 1.5, 10]]}}
        candles = FyersDataProvider._parse_history_candles(payload)
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0][4], 1.5)


if __name__ == "__main__":
    unittest.main()
