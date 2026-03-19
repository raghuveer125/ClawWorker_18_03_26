import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))

from shared_project_engine.indices import get_watchlist
from trading.screener import _build_watchlist_baskets


class ScreenerWatchlistTests(unittest.TestCase):
    def test_default_baskets_use_shared_index_watchlists(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in (
                "FYERS_WATCHLIST_SENSEX",
                "FYERS_WATCHLIST_NIFTY50",
                "FYERS_WATCHLIST_BANKNIFTY",
            ):
                os.environ.pop(key, None)

            baskets = _build_watchlist_baskets()

        self.assertEqual(list(baskets.keys()), ["SENSEX", "NIFTY50", "BANKNIFTY"])
        self.assertEqual(baskets["SENSEX"], get_watchlist("SENSEX"))
        self.assertEqual(baskets["NIFTY50"], get_watchlist("NIFTY50"))
        self.assertEqual(baskets["BANKNIFTY"], get_watchlist("BANKNIFTY"))

    def test_index_specific_env_override_replaces_single_basket(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ["FYERS_WATCHLIST_NIFTY50"] = "NSE:RELIANCE-EQ,NSE:TCS-EQ"
            os.environ.pop("FYERS_WATCHLIST_SENSEX", None)
            os.environ.pop("FYERS_WATCHLIST_BANKNIFTY", None)

            baskets = _build_watchlist_baskets()

        self.assertEqual(baskets["NIFTY50"], ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"])
        self.assertEqual(baskets["SENSEX"], get_watchlist("SENSEX"))
        self.assertEqual(baskets["BANKNIFTY"], get_watchlist("BANKNIFTY"))

    def test_explicit_watchlist_argument_uses_custom_basket(self):
        baskets = _build_watchlist_baskets("NSE:RELIANCE-EQ,NSE:TCS-EQ")

        self.assertEqual(
            baskets,
            {
                "CUSTOM": ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"],
            },
        )


if __name__ == "__main__":
    unittest.main()
