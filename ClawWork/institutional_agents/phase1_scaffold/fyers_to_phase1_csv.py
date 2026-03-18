from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from shared_project_engine.indices import get_market_index_config
    from shared_project_engine.market import MarketDataClient
except ImportError:
    get_market_index_config = None
    MarketDataClient = None


IST = timezone(timedelta(hours=5, minutes=30))


def _load_env_file(path: Path) -> List[str]:
    loaded: List[str] = []
    if not path.exists() or not path.is_file():
        return loaded

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


@dataclass
class ExportRow:
    timestamp: str
    underlying: str
    ltp: float
    prev_close: float
    session: str
    daily_realized_pnl_pct: float
    bid_ask_spread_bps: float


class FyersHistoryClient:
    def __init__(self) -> None:
        default_env_file = _PROJECT_ROOT / ".env"
        env_file = str(default_env_file) if default_env_file.exists() else None
        self.client = (
            MarketDataClient(env_file=env_file, fallback_to_local=bool(os.getenv("FYERS_ACCESS_TOKEN")))
            if MarketDataClient is not None
            else None
        )

    def history(self, symbol: str, resolution: str, range_from: str, range_to: str) -> Dict[str, Any]:
        if self.client is None:
            return {
                "success": False,
                "error": "Shared market client is unavailable",
                "message": "Start the market adapter service or set FYERS credentials for local fallback",
            }
        try:
            payload = self.client.get_history_range(
                symbol=symbol,
                resolution=resolution,
                range_from=range_from,
                range_to=range_to,
                date_format="1",
                cont_flag="1",
            )
        except Exception as exc:
            return {"success": False, "error": f"Shared history fetch failed: {exc}"}

        if _parse_candles(payload):
            source = str(payload.get("_source", "local"))
            return {
                "success": True,
                "data": payload,
                "history_source_used": source,
                "history_endpoint_used": source,
            }

        return {"success": False, "error": json.dumps(payload)}


def _resolve_symbol_map() -> Dict[str, str]:
    if get_market_index_config is not None:
        return {
            "NIFTY50": str(get_market_index_config("NIFTY50").get("spot_symbol", "NSE:NIFTY50-INDEX")),
            "BANKNIFTY": str(get_market_index_config("BANKNIFTY").get("spot_symbol", "NSE:NIFTYBANK-INDEX")),
            "SENSEX": str(get_market_index_config("SENSEX").get("spot_symbol", "BSE:SENSEX-INDEX")),
        }
    return {
        "NIFTY50": os.getenv("FYERS_INDEX_SYMBOL_NIFTY50", "NSE:NIFTY50-INDEX"),
        "BANKNIFTY": os.getenv("FYERS_INDEX_SYMBOL_BANKNIFTY", "NSE:NIFTYBANK-INDEX"),
        "SENSEX": os.getenv("FYERS_INDEX_SYMBOL_SENSEX", "BSE:SENSEX-INDEX"),
    }


def _parse_candles(payload: Dict[str, Any]) -> List[List[Any]]:
    data = payload.get("data", {})
    if isinstance(data, dict) and isinstance(data.get("candles"), list):
        return data["candles"]
    if isinstance(payload.get("candles"), list):
        return payload["candles"]
    return []


def _epoch_to_ist(ts_value: Any) -> datetime:
    ts_float = float(ts_value)
    if ts_float > 1_000_000_000_000:
        ts_float /= 1000.0
    return datetime.fromtimestamp(ts_float, tz=timezone.utc).astimezone(IST)


def _session_for_timestamp(dt_obj: datetime, resolution: str) -> str:
    if resolution.upper() in {"D", "1D"}:
        return "CLOSE"
    local_time = dt_obj.timetz().replace(tzinfo=None)
    if local_time < time(10, 30):
        return "OPEN"
    if local_time < time(14, 0):
        return "MIDDAY"
    return "CLOSE"


def _normalize_timestamp(dt_obj: datetime, resolution: str) -> datetime:
    if resolution.upper() in {"D", "1D"}:
        return dt_obj.replace(hour=15, minute=20, second=0, microsecond=0)
    return dt_obj.replace(second=0, microsecond=0)


def _build_rows(
    underlying: str,
    candles: List[List[Any]],
    resolution: str,
    daily_realized_pnl_pct: float,
    spread_bps: float,
) -> List[ExportRow]:
    rows: List[ExportRow] = []
    prev_close_value: Optional[float] = None

    for candle in candles:
        if not isinstance(candle, list) or len(candle) < 5:
            continue
        dt_obj = _normalize_timestamp(_epoch_to_ist(candle[0]), resolution)
        open_px = float(candle[1])
        close_px = float(candle[4])
        prev_close = prev_close_value if prev_close_value is not None else open_px
        session = _session_for_timestamp(dt_obj, resolution)

        rows.append(
            ExportRow(
                timestamp=dt_obj.isoformat(),
                underlying=underlying,
                ltp=round(close_px, 4),
                prev_close=round(prev_close, 4),
                session=session,
                daily_realized_pnl_pct=round(daily_realized_pnl_pct, 4),
                bid_ask_spread_bps=round(spread_bps, 4),
            )
        )
        prev_close_value = close_px

    return rows


def _write_csv(path: Path, rows: List[ExportRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "timestamp",
        "underlying",
        "ltp",
        "prev_close",
        "session",
        "daily_realized_pnl_pct",
        "bid_ask_spread_bps",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "timestamp": row.timestamp,
                "underlying": row.underlying,
                "ltp": row.ltp,
                "prev_close": row.prev_close,
                "session": row.session,
                "daily_realized_pnl_pct": row.daily_realized_pnl_pct,
                "bid_ask_spread_bps": row.bid_ask_spread_bps,
            })


def main() -> int:
    parser = argparse.ArgumentParser(description="Export FYERS historical candles to Phase 1 CSV format")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional .env path. Defaults to ClawWork/.env when available.",
    )
    parser.add_argument("--from-date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--resolution", default="D", help="FYERS resolution, e.g. D, 60, 15")
    parser.add_argument(
        "--underlyings",
        default="NIFTY50,BANKNIFTY,SENSEX",
        help="Comma-separated underlyings from {NIFTY50,BANKNIFTY,SENSEX}",
    )
    parser.add_argument("--daily-realized-pnl-pct", type=float, default=0.0)
    parser.add_argument("--spread-bps", type=float, default=25.0)
    parser.add_argument("--min-rows", type=int, default=20)
    parser.add_argument("--out-csv", default="sample_batch_input_real_fyers.csv")
    args = parser.parse_args()

    default_env_file = Path(__file__).resolve().parents[2] / ".env"
    env_file = Path(args.env_file).expanduser().resolve() if args.env_file else default_env_file
    loaded_env_keys = _load_env_file(env_file)

    symbol_map = _resolve_symbol_map()
    selected = [item.strip().upper() for item in args.underlyings.split(",") if item.strip()]
    invalid = [name for name in selected if name not in symbol_map]
    if invalid:
        print(json.dumps({"success": False, "error": f"Invalid underlyings: {invalid}"}, indent=2))
        return 2

    client = FyersHistoryClient()
    all_rows: List[ExportRow] = []
    endpoint_usage: Dict[str, str] = {}
    fetch_errors: Dict[str, Any] = {}

    for underlying in selected:
        symbol = symbol_map[underlying]
        result = client.history(
            symbol=symbol,
            resolution=args.resolution,
            range_from=args.from_date,
            range_to=args.to_date,
        )
        if not result.get("success"):
            fetch_errors[underlying] = result
            continue

        endpoint_usage[underlying] = str(result.get("history_endpoint_used", ""))
        candles = _parse_candles(result)
        rows = _build_rows(
            underlying=underlying,
            candles=candles,
            resolution=args.resolution,
            daily_realized_pnl_pct=args.daily_realized_pnl_pct,
            spread_bps=args.spread_bps,
        )
        all_rows.extend(rows)

    all_rows.sort(key=lambda row: row.timestamp)

    out_path = Path(args.out_csv)
    if all_rows:
        _write_csv(out_path, all_rows)

    covered_dates = sorted({row.timestamp[:10] for row in all_rows})
    payload = {
        "success": len(fetch_errors) == 0 and len(all_rows) >= args.min_rows,
        "output_csv": str(out_path),
        "env_file_used": str(env_file) if env_file.exists() else None,
        "env_keys_loaded_count": len(loaded_env_keys),
        "fyers_access_token_present": bool(os.getenv("FYERS_ACCESS_TOKEN")),
        "fyers_app_id_present": bool(os.getenv("FYERS_APP_ID") or os.getenv("FYERS_CLIENT_ID")),
        "requested_underlyings": selected,
        "rows": len(all_rows),
        "min_rows_required": args.min_rows,
        "covered_trading_days": len(covered_dates),
        "window": {"from": args.from_date, "to": args.to_date},
        "resolution": args.resolution,
        "history_endpoint_used": endpoint_usage,
        "errors": fetch_errors,
    }
    print(json.dumps(payload, indent=2))

    if fetch_errors:
        return 2
    return 0 if len(all_rows) >= args.min_rows else 3


if __name__ == "__main__":
    raise SystemExit(main())
