import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from shared_project_engine.indices import config

class _FakeResponse:
    def __init__(self, json_data=None, text="", status_code=200):
        self._json_data = json_data or {}
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeNseSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):  # noqa: ANN001
        self.calls.append((url, params, timeout))
        if "option-chain-indices" not in url:
            return _FakeResponse()

        symbol = (params or {}).get("symbol")
        payloads = {
            "NIFTY": {"records": {"expiryDates": ["24-Mar-2026", "31-Mar-2026"]}},
            "BANKNIFTY": {"records": {"expiryDates": ["25-Mar-2026"]}},
            "FINNIFTY": {"records": {"expiryDates": ["24-Mar-2026"]}},
            "MIDCPNIFTY": {"records": {"expiryDates": ["23-Mar-2026"]}},
        }
        return _FakeResponse(json_data=payloads.get(symbol, {"records": {"expiryDates": []}}))

    def close(self):
        return None


class _FakeBseSession:
    def get(self, url, timeout=None):  # noqa: ANN001
        return _FakeResponse(text="SENSEX2631980000CE SENSEX2632690000PE")

    def close(self):
        return None


class ExchangeExpiryScheduleTests(unittest.TestCase):
    def test_fetch_nse_exchange_expiries_parses_option_chain_dates(self):
        with patch.object(config, "requests", object()):
            expiries = config.fetch_nse_exchange_expiries(
                session=_FakeNseSession(),
                today=date(2026, 3, 19),
            )

        self.assertEqual(expiries["NIFTY50"], "2026-03-24")
        self.assertEqual(expiries["BANKNIFTY"], "2026-03-25")
        self.assertEqual(expiries["FINNIFTY"], "2026-03-24")
        self.assertEqual(expiries["MIDCPNIFTY"], "2026-03-23")

    def test_fetch_bse_exchange_expiries_parses_sensex_series_codes(self):
        with patch.object(config, "requests", object()):
            expiries = config.fetch_bse_exchange_expiries(
                session=_FakeBseSession(),
                today=date(2026, 3, 19),
            )

        self.assertEqual(expiries, {"SENSEX": "2026-03-19"})

    def test_get_expiry_snapshot_marks_sensex_as_todays_expiry(self):
        fake_snapshot = {
            "data": {
                "SENSEX": "2026-03-19",
                "NIFTY50": "2026-03-24",
                "BANKNIFTY": "2026-03-25",
                "FINNIFTY": "2026-03-24",
                "MIDCPNIFTY": "2026-03-23",
            },
            "fetched_at": "2026-03-19T09:15:00+05:30",
        }

        with patch.object(config, "_ist_today", return_value=date(2026, 3, 19)), patch.object(
            config, "_load_expiry_cache", return_value=None
        ), patch.object(config, "_fetch_exchange_expiry_snapshot", return_value=fake_snapshot):
            snapshot = config.get_expiry_snapshot()

        self.assertEqual(snapshot["todaysExpiry"], ["SENSEX"])
        self.assertEqual(snapshot["sourceStatus"], "exchange")
        self.assertTrue(snapshot["expirySchedule"]["SENSEX"]["is_expiry_today"])
        self.assertFalse(snapshot["expirySchedule"]["NIFTY50"]["is_expiry_today"])
        self.assertEqual(snapshot["expirySchedule"]["SENSEX"]["source"], "exchange")

    def test_get_expiry_snapshot_returns_unavailable_when_exchange_data_missing(self):
        with patch.object(config, "_ist_today", return_value=date(2026, 3, 19)), patch.object(
            config, "_load_expiry_cache", return_value=None
        ), patch.object(
            config,
            "_fetch_exchange_expiry_snapshot",
            return_value={"data": {}, "fetched_at": None},
        ):
            snapshot = config.get_expiry_snapshot()

        self.assertEqual(snapshot["todaysExpiry"], [])
        self.assertEqual(snapshot["sourceStatus"], "unavailable")
        self.assertEqual(snapshot["expirySchedule"]["SENSEX"]["source"], "unavailable")
        self.assertIsNone(snapshot["expirySchedule"]["NIFTY50"]["next_expiry"])

    def test_get_expiry_snapshot_uses_fyers_when_exchange_data_missing(self):
        fyers_snapshot = {
            "data": {
                "SENSEX": {"next_expiry": "2026-03-19", "source": "fyers"},
                "NIFTY50": {"next_expiry": "2026-03-24", "source": "fyers"},
            },
            "fetched_at": "2026-03-19T18:05:00+05:30",
        }

        with patch.object(config, "_ist_today", return_value=date(2026, 3, 19)), patch.object(
            config, "_load_expiry_cache", return_value=None
        ), patch.object(config, "_fetch_exchange_expiry_snapshot", return_value=fyers_snapshot):
            snapshot = config.get_expiry_snapshot()

        self.assertEqual(snapshot["todaysExpiry"], ["SENSEX"])
        self.assertEqual(snapshot["sourceStatus"], "fyers")
        self.assertEqual(snapshot["expirySchedule"]["SENSEX"]["source"], "fyers")
        self.assertEqual(snapshot["expirySchedule"]["NIFTY50"]["next_expiry"], "2026-03-24")

    def test_get_expiry_snapshot_reuses_fresh_disk_cache_before_network(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            cache_file = cache_dir / "expiry_schedule.json"

            cache_dir.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "data": {"SENSEX": "2026-03-19", "NIFTY50": "2026-03-24"},
                        "fetched_at": "2026-03-19T09:15:00+05:30",
                        "timestamp": 1_763_264_100.0,
                    },
                    handle,
                )

            with patch.object(config, "_EXPIRY_CACHE_DIR", cache_dir), patch.object(
                config, "_EXPIRY_CACHE_FILE", cache_file
            ), patch.object(config, "_ist_today", return_value=date(2026, 3, 19)), patch(
                "shared_project_engine.indices.config.time.time", return_value=1_763_264_200.0
            ), patch.object(config, "_fetch_exchange_expiry_snapshot") as fetch_snapshot:
                snapshot = config.get_expiry_snapshot()

        fetch_snapshot.assert_not_called()
        self.assertEqual(snapshot["todaysExpiry"], ["SENSEX"])
        self.assertEqual(snapshot["sourceStatus"], "partial")


if __name__ == "__main__":
    unittest.main()
