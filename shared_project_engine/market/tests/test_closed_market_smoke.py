from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[4]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
LIVEBENCH_ROOT = PROJECT_ROOT / "ClawWork" / "livebench"

for candidate in (REPO_ROOT, LIVEBENCH_ROOT):
    path_str = str(candidate)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from ClawWork_FyersN7.ClawWork.livebench.api.server import DATA_PATH, FYERSN7_DATA_PATH, app
from ClawWork_FyersN7.shared_project_engine.market.adapter import MarketDataAdapter
from ClawWork_FyersN7.shared_project_engine.market.audit import build_report as build_audit_report
from ClawWork_FyersN7.shared_project_engine.market.service import MarketDataService


class FakeAdapter:
    def __init__(self) -> None:
        self.quote_calls = {}
        self.quotes_calls = {}
        self.history_calls = {}
        self.option_calls = {}

    def _cache_hit(self, store: dict[str, int], key: str) -> bool:
        count = store.get(key, 0)
        store[key] = count + 1
        return count > 0

    def get_quote(self, symbol: str, ttl_seconds=None):  # noqa: ANN001
        cache_hit = self._cache_hit(self.quote_calls, symbol)
        return {"symbol": symbol, "ltp": 100.0, "cache_hit": cache_hit}

    def get_quotes(self, symbols: str, ttl_seconds=None):  # noqa: ANN001
        cache_hit = self._cache_hit(self.quotes_calls, symbols)
        normalized = [item.strip() for item in symbols.split(",") if item.strip()]
        return {"symbols": normalized, "data": {}, "cache_hit": cache_hit}

    def get_history_snapshot(self, symbol: str, resolution: str, lookback_days: int, ttl_seconds=None):  # noqa: ANN001
        key = f"{symbol}:{resolution}:{lookback_days}"
        cache_hit = self._cache_hit(self.history_calls, key)
        return {"candles": [[1, 2, 3, 4, 5]], "_cache_hit": cache_hit}

    def get_option_chain_snapshot(self, symbol: str, strike_count: int, ttl_seconds=None):  # noqa: ANN001
        key = f"{symbol}:{strike_count}"
        cache_hit = self._cache_hit(self.option_calls, key)
        return {"options": [{"symbol": symbol}], "_cache_hit": cache_hit}

    def futures_candidates(self, index_name: str, now_local=None):  # noqa: ANN001
        return [f"{index_name}-FUT"]


class ClosedMarketSmokeTest(unittest.TestCase):
    def test_one_minute_history_ttl_aligns_to_next_candle(self) -> None:
        with patch("ClawWork_FyersN7.shared_project_engine.market.adapter.time.time", return_value=130.0):
            ttl = MarketDataAdapter._history_snapshot_ttl_seconds(
                resolution="1",
                requested_ttl_seconds=None,
                fallback_ttl_seconds=15.0,
            )
        self.assertEqual(ttl, 49.0)

    def test_explicit_history_ttl_is_preserved(self) -> None:
        ttl = MarketDataAdapter._history_snapshot_ttl_seconds(
            resolution="1",
            requested_ttl_seconds=20.0,
            fallback_ttl_seconds=15.0,
        )
        self.assertEqual(ttl, 20.0)

    def test_market_service_metrics_persist_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            metrics_path = Path(tmpdir) / "metrics.json"
            service = MarketDataService(
                adapter=FakeAdapter(),
                metrics_path=str(metrics_path),
                persist_every_requests=1,
            )

            service.quotes("NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX", ttl_seconds=30)
            service.quotes("NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX", ttl_seconds=30)
            service.future_quote(index_name="NIFTY50", ttl_seconds=30)
            service.future_quote(index_name="NIFTY50", ttl_seconds=30)
            service.history("NSE:NIFTY50-INDEX", resolution="5", lookback_days=5, ttl_seconds=30)
            service.history("NSE:NIFTY50-INDEX", resolution="5", lookback_days=5, ttl_seconds=30)
            service.option_chain("NSE:NIFTY50-INDEX", strike_count=8, ttl_seconds=30)
            service.option_chain("NSE:NIFTY50-INDEX", strike_count=8, ttl_seconds=30)
            service.flush_metrics()

            persisted = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertEqual(sum(persisted["request_counts"].values()), 8)

            reloaded = MarketDataService(
                adapter=FakeAdapter(),
                metrics_path=str(metrics_path),
                persist_every_requests=1,
            )
            metrics = reloaded.get_metrics()
            self.assertEqual(metrics["total_requests"], 8)
            self.assertEqual(metrics["session_total_requests"], 0)
            self.assertEqual(metrics["total_duplicate_suppressed"], 4)
            self.assertTrue(metrics["metrics_path"].endswith("metrics.json"))

    def test_aggregated_api_endpoints_have_closed_market_payloads(self) -> None:
        client = TestClient(app)
        dates = sorted(
            [path.name for path in FYERSN7_DATA_PATH.iterdir() if path.is_dir() and path.name.startswith("202")],
            reverse=True,
        )
        self.assertTrue(dates)

        snapshot = client.get(f"/api/fyersn7/snapshot/{dates[0]}?latest_only=true")
        snapshot.raise_for_status()
        snapshot_payload = snapshot.json()
        self.assertIn("signals", snapshot_payload)
        self.assertIn("trades", snapshot_payload)
        self.assertIn("events", snapshot_payload)

        live_signals = client.get("/api/fyersn7/live-signals/NIFTY50")
        live_signals.raise_for_status()
        live_payload = live_signals.json()
        self.assertEqual(live_payload["index"], "NIFTY50")
        self.assertIn("rows", live_payload)

        agents = sorted([path.name for path in DATA_PATH.iterdir() if path.is_dir()])
        self.assertTrue(agents)
        supplemental = client.get(f"/api/agents/{agents[0]}/dashboard-supplemental")
        supplemental.raise_for_status()
        supplemental_payload = supplemental.json()
        self.assertIn("fyers_screener", supplemental_payload)
        self.assertIn("institutional_shadow", supplemental_payload)
        self.assertIn("market_session", supplemental_payload)

    def test_market_audit_has_no_issues(self) -> None:
        report = build_audit_report(PROJECT_ROOT)
        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["issues"])


if __name__ == "__main__":
    unittest.main()
