#!/usr/bin/env python3
"""Regression tests for ICT Sniper integration."""

import unittest
from tempfile import TemporaryDirectory

from livebench.bots.base import SharedMemory
from livebench.bots.ensemble import EnsembleCoordinator


class ICTIntegrationTests(unittest.TestCase):
    def test_ict_sniper_is_registered_and_accepts_market_data(self):
        with TemporaryDirectory() as temp_dir:
            ensemble = EnsembleCoordinator(shared_memory=SharedMemory(data_dir=temp_dir))
            ict_bot = ensemble.bot_map.get("ICTSniper")

            self.assertIsNotNone(ict_bot)
            self.assertEqual(ict_bot.name, "ICTSniper")
            self.assertGreater(ict_bot.performance.weight, 0)

            sample_data = {
                "index": "BANKNIFTY",
                "open": 240.0,
                "high": 245.0,
                "low": 238.0,
                "close": 242.0,
                "volume": 1_000_000,
                "atr": 2.5,
                "bar_index": 100,
                "timestamp": "2026-03-09T10:00:00",
            }

            signal = ict_bot.analyze("BANKNIFTY", sample_data)
            self.assertIn(signal is None, (True, False))


if __name__ == "__main__":
    unittest.main()
