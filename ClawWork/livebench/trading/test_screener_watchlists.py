import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))

from shared_project_engine.indices import get_watchlist
from trading.screener import _build_watchlist_baskets
from trading.fyers_client import market_data_client_kwargs


class ScreenerWatchlistTests(unittest.TestCase):
    def test_market_data_client_kwargs_enable_local_fallback_from_env_file(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        env_path = Path(temp_dir.name) / ".env"
        env_path.write_text("FYERS_ACCESS_TOKEN=test-token\n", encoding="utf-8")

        kwargs = market_data_client_kwargs(str(env_path))

        self.assertEqual(kwargs["env_file"], str(env_path))
        self.assertTrue(kwargs["fallback_to_local"])
        self.assertFalse(kwargs["strict_mode"])

    def test_market_data_client_kwargs_disable_local_fallback_without_token(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        env_path = Path(temp_dir.name) / ".env"
        env_path.write_text("OPENAI_API_KEY=test\n", encoding="utf-8")

        kwargs = market_data_client_kwargs(str(env_path))

        self.assertEqual(kwargs["env_file"], str(env_path))
        self.assertFalse(kwargs["fallback_to_local"])
        self.assertFalse(kwargs["strict_mode"])

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
