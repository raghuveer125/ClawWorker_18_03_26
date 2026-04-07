import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import server
import shared_project_engine.indices as shared_indices


class IndexExpiryScheduleEndpointTests(unittest.TestCase):
    def test_exchange_success_path_returns_sensex_for_march_19(self):
        snapshot = {
            "expirySchedule": {
                "SENSEX": {
                    "exchange": "BSE",
                    "source": "exchange",
                    "next_expiry": "2026-03-19",
                    "weekday": 3,
                    "weekday_name": "Thursday",
                    "weekday_short": "Thu",
                    "is_expiry_today": True,
                },
                "NIFTY50": {
                    "exchange": "NSE",
                    "source": "exchange",
                    "next_expiry": "2026-03-24",
                    "weekday": 1,
                    "weekday_name": "Tuesday",
                    "weekday_short": "Tue",
                    "is_expiry_today": False,
                },
            },
            "todaysExpiry": ["SENSEX"],
            "sourceStatus": "partial",
            "fetchedAt": "2026-03-19T09:15:00+05:30",
        }

        with patch.object(shared_indices, "get_expiry_snapshot", return_value=snapshot):
            result = asyncio.run(server.get_index_expiry_schedule())

        self.assertEqual(result["todaysExpiry"], ["SENSEX"])
        self.assertEqual(result["sourceStatus"], "partial")
        self.assertTrue(result["expirySchedule"]["SENSEX"]["is_expiry_today"])
        self.assertFalse(result["expirySchedule"]["NIFTY50"]["is_expiry_today"])

    def test_unavailable_path_returns_empty_todays_expiry(self):
        snapshot = {
            "expirySchedule": {
                "SENSEX": {
                    "exchange": "BSE",
                    "source": "unavailable",
                    "next_expiry": None,
                    "weekday": None,
                    "weekday_name": None,
                    "weekday_short": None,
                    "is_expiry_today": False,
                },
                "NIFTY50": {
                    "exchange": "NSE",
                    "source": "unavailable",
                    "next_expiry": None,
                    "weekday": None,
                    "weekday_name": None,
                    "weekday_short": None,
                    "is_expiry_today": False,
                },
            },
            "todaysExpiry": [],
            "sourceStatus": "unavailable",
            "fetchedAt": None,
        }

        with patch.object(shared_indices, "get_expiry_snapshot", return_value=snapshot):
            result = asyncio.run(server.get_index_expiry_schedule())

        self.assertEqual(result["todaysExpiry"], [])
        self.assertEqual(result["sourceStatus"], "unavailable")
        self.assertEqual(result["expirySchedule"]["SENSEX"]["source"], "unavailable")
        self.assertIsNone(result["expirySchedule"]["NIFTY50"]["weekday"])

    def test_fyers_fallback_path_still_returns_upcoming_expiry_dates(self):
        snapshot = {
            "expirySchedule": {
                "SENSEX": {
                    "exchange": "BSE",
                    "source": "fyers",
                    "next_expiry": "2026-03-19",
                    "weekday": 3,
                    "weekday_name": "Thursday",
                    "weekday_short": "Thu",
                    "is_expiry_today": True,
                },
                "NIFTY50": {
                    "exchange": "NSE",
                    "source": "fyers",
                    "next_expiry": "2026-03-24",
                    "weekday": 1,
                    "weekday_name": "Tuesday",
                    "weekday_short": "Tue",
                    "is_expiry_today": False,
                },
            },
            "todaysExpiry": ["SENSEX"],
            "sourceStatus": "fyers",
            "fetchedAt": "2026-03-19T18:05:00+05:30",
        }

        with patch.object(shared_indices, "get_expiry_snapshot", return_value=snapshot):
            result = asyncio.run(server.get_index_expiry_schedule())

        self.assertEqual(result["sourceStatus"], "fyers")
        self.assertEqual(result["todaysExpiry"], ["SENSEX"])
        self.assertEqual(result["expirySchedule"]["NIFTY50"]["source"], "fyers")
        self.assertEqual(result["expirySchedule"]["NIFTY50"]["next_expiry"], "2026-03-24")


if __name__ == "__main__":
    unittest.main()
