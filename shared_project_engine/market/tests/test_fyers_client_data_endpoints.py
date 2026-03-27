from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[4]
PROJECT_ROOT = Path(__file__).resolve().parents[3]

for candidate in (REPO_ROOT, PROJECT_ROOT):
    path_str = str(candidate)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from ClawWork_FyersN7.shared_project_engine.auth.fyers_client import FyersClient
from ClawWork_FyersN7.shared_project_engine.market.adapter import MarketDataAdapter


class FyersClientDataEndpointTest(unittest.TestCase):
    def test_adapter_forwards_tls_settings_to_shared_client(self) -> None:
        with patch("ClawWork_FyersN7.shared_project_engine.market.adapter.FyersClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.access_token = "token"
            mock_client.client_id = "client"

            adapter = MarketDataAdapter(
                access_token="token",
                client_id="client",
                insecure=True,
                ca_bundle="/tmp/corp.pem",
            )

        self.assertEqual(adapter.access_token, "token")
        self.assertEqual(adapter.client_id, "client")
        mock_client_cls.assert_called_once_with(
            access_token="token",
            client_id="client",
            env_file=None,
            insecure=True,
            ca_bundle="/tmp/corp.pem",
        )

    def test_quotes_skip_sdk_when_custom_tls_is_requested(self) -> None:
        client = FyersClient(
            access_token="token",
            client_id="client",
            insecure=True,
        )

        with patch("ClawWork_FyersN7.shared_project_engine.auth.fyers_client.HAS_FYERS_SDK", True), \
             patch.object(client, "_request", return_value={"success": True, "data": {"d": [{"n": "NSE:NIFTY50-INDEX"}]}}):
            result = client.quotes("NSE:NIFTY50-INDEX")

        self.assertTrue(result["success"])

    def test_history_prefers_data_endpoint_and_uses_date_format_one_for_dates(self) -> None:
        client = FyersClient(
            access_token="token",
            client_id="client",
            api_base_url="https://api-t1.fyers.in/api/v3",
        )

        with patch.object(client, "_request", return_value={"success": True, "data": {"candles": [[1, 2, 3, 4, 5, 6]]}}) as mock_request:
            result = client.history(
                symbol="BSE:SENSEX-INDEX",
                resolution="5",
                from_date="2026-03-10",
                to_date="2026-03-13",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["endpoint_used"], "GET https://api-t1.fyers.in/data/history")
        mock_request.assert_called_once_with(
            "GET",
            "https://api-t1.fyers.in/data/history",
            payload=None,
            params={
                "symbol": "BSE:SENSEX-INDEX",
                "resolution": "5",
                "date_format": "1",
                "cont_flag": "1",
                "range_from": "2026-03-10",
                "range_to": "2026-03-13",
            },
        )

    def test_option_chain_prefers_data_endpoint(self) -> None:
        client = FyersClient(
            access_token="token",
            client_id="client",
            api_base_url="https://api-t1.fyers.in/api/v3",
        )

        with patch.object(client, "_request", return_value={"success": True, "data": {"code": 200, "data": {"optionsChain": []}}}) as mock_request:
            result = client.option_chain(symbol="BSE:SENSEX-INDEX", strike_count=8)

        self.assertTrue(result["success"])
        self.assertEqual(result["endpoint_used"], "GET https://api-t1.fyers.in/data/options-chain-v3")
        mock_request.assert_called_once_with(
            "GET",
            "https://api-t1.fyers.in/data/options-chain-v3",
            payload=None,
            params={
                "symbol": "BSE:SENSEX-INDEX",
                "strikecount": 8,
                "timestamp": "",
            },
        )

    def test_adapter_history_fallback_uses_data_endpoint(self) -> None:
        adapter = MarketDataAdapter(access_token="token", client_id="client")

        with patch.object(adapter.client, "_request", return_value={"success": True, "data": {"candles": [[1, 2, 3, 4, 5, 6]]}}) as mock_request:
            payload = adapter._fetch_history_payload(  # noqa: SLF001
                {
                    "symbol": "BSE:SENSEX-INDEX",
                    "resolution": "5",
                    "date_format": "0",
                    "range_from": "100",
                    "range_to": "200",
                    "cont_flag": "1",
                }
            )

        self.assertEqual(payload["candles"][0][0], 1)
        mock_request.assert_called_once_with(
            "GET",
            "https://api-t1.fyers.in/data/history",
            params={
                "symbol": "BSE:SENSEX-INDEX",
                "resolution": "5",
                "date_format": "0",
                "range_from": "100",
                "range_to": "200",
                "cont_flag": "1",
            },
        )

    def test_invalid_history_cache_payload_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = MarketDataAdapter(access_token="token", client_id="client", cache_dir=tmpdir)
            key = '{"lookback_days": 5, "resolution": "5", "symbol": "BSE:SENSEX-INDEX"}'
            adapter.cache.set("history", key, {"raw": "404 page not found"})

            with patch.object(adapter, "_fetch_history_payload", return_value={"candles": [[1, 2, 3, 4, 5, 6]]}) as mock_fetch:
                payload = adapter.get_history_snapshot("BSE:SENSEX-INDEX", resolution="5", lookback_days=5, ttl_seconds=60)

        self.assertFalse(payload["_cache_hit"])
        self.assertEqual(len(payload["candles"]), 1)
        mock_fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
