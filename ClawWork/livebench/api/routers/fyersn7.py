"""FyersN7 signal/trade/event endpoints (~10 routes)."""

import csv
import json
from datetime import datetime
from functools import lru_cache
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from ..deps import (
    FYERS_DATA_PATH,
    FYERSN7_DATA_PATH,
    _latest_fyers_screener_failure,
    _to_float,
    _to_int,
)

router = APIRouter(prefix="/api", tags=["fyersn7"])

# ---------------------------------------------------------------------------
# CSV helpers (fyersn7-only)
# ---------------------------------------------------------------------------


def _read_csv_as_dicts(file_path) -> List[Dict]:
    """Read CSV file and return list of dicts."""
    from pathlib import Path
    file_path = Path(file_path)
    if not file_path.exists():
        return []
    return list(_read_csv_as_dicts_cached(str(file_path), file_path.stat().st_mtime_ns))


@lru_cache(maxsize=64)
def _read_csv_as_dicts_cached(path_str: str, mtime_ns: int) -> tuple[Dict, ...]:
    """Read and cache CSV rows by file path + mtime."""
    rows = []
    with open(path_str, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return tuple(rows)


def _dedupe_csv_rows(rows: List[Dict], key_fields: List[str]) -> List[Dict]:
    deduped: List[Dict] = []
    seen = set()
    for row in rows:
        key = tuple((row.get(field, "") or "") for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _dedupe_trade_rows(rows: List[Dict]) -> List[Dict]:
    return _dedupe_csv_rows(
        rows,
        [
            "trade_id", "symbol", "side", "strike", "qty",
            "entry_date", "entry_time", "entry_price",
            "exit_date", "exit_time", "exit_price", "exit_reason",
        ],
    )


def _dedupe_event_rows(rows: List[Dict]) -> List[Dict]:
    return _dedupe_csv_rows(
        rows,
        [
            "event_date", "event_time", "event_type", "side",
            "strike", "entry", "exit", "reason",
        ],
    )


def _transform_signal_row(row: Dict) -> Dict:
    """Transform a decision_journal row to the frontend-friendly signal shape."""
    return {
        "date": row.get("date", ""),
        "time": row.get("time", ""),
        "side": row.get("side", ""),
        "strike": _to_int(row.get("strike")),
        "strike_pcr": _to_float(row.get("strike_pcr")),
        "entry": _to_float(row.get("entry")),
        "sl": _to_float(row.get("sl")),
        "t1": _to_float(row.get("t1")),
        "t2": _to_float(row.get("t2")),
        "confidence": _to_int(row.get("confidence")),
        "status": row.get("status", ""),
        "score": row.get("score", ""),
        "action": row.get("action", ""),
        "stable": row.get("stable", ""),
        "cooldown_sec": _to_int(row.get("cooldown_sec")),
        "entry_ready": row.get("entry_ready", "N") == "Y",
        "selected": row.get("selected", "N") == "Y",
        "bid": _to_float(row.get("bid")),
        "ask": _to_float(row.get("ask")),
        "spread_pct": _to_float(row.get("spread_pct")),
        "iv": _to_float(row.get("iv")),
        "delta": _to_float(row.get("delta")),
        "gamma": _to_float(row.get("gamma")),
        "theta_day": _to_float(row.get("theta_day")),
        "decay_pct": _to_float(row.get("decay_pct")),
        "vote_ce": _to_int(row.get("vote_ce")),
        "vote_pe": _to_int(row.get("vote_pe")),
        "vote_side": row.get("vote_side", ""),
        "vote_diff": _to_int(row.get("vote_diff")),
        "vol_dom": row.get("vol_dom", ""),
        "vol_switch": row.get("vol_switch", "N") == "Y",
        "flow_match": row.get("flow_match", ""),
        "fvg_side": row.get("fvg_side", ""),
        "fvg_active": row.get("fvg_active", ""),
        "fvg_gap": _to_float(row.get("fvg_gap")),
        "fvg_distance": _to_float(row.get("fvg_distance")),
        "fvg_distance_atr": _to_float(row.get("fvg_distance_atr")),
        "fvg_plus": row.get("fvg_plus", "N") == "Y",
        "learn_prob": _to_float(row.get("learn_prob")),
        "learn_gate": row.get("learn_gate", ""),
        "outside_window": row.get("outside_window", ""),
        "reason": row.get("reason", ""),
        "outcome": row.get("outcome", ""),
        "spot": _to_float(row.get("spot")),
        "vix": _to_float(row.get("vix")),
        "net_pcr": _to_float(row.get("net_pcr")),
        "max_pain": _to_float(row.get("max_pain")),
        "max_pain_dist": _to_float(row.get("max_pain_dist")),
        "fut_symbol": row.get("fut_symbol", ""),
        "contract_symbol": row.get("contract_symbol", "") or row.get("option_symbol", ""),
        "option_expiry": row.get("option_expiry", "") or row.get("expiry", "") or row.get("expiry_date", ""),
        "option_expiry_code": row.get("option_expiry_code", ""),
        "fut_basis": _to_float(row.get("fut_basis")),
        "fut_basis_pct": _to_float(row.get("fut_basis_pct")),
    }


def _is_valid_trade_signal(row: Dict) -> bool:
    side = str(row.get("side", "")).upper()
    strike = row.get("strike")
    return side in ("CE", "PE") and strike not in (None, "", "None")


def _signal_row_key(row: Dict) -> tuple[str, str]:
    return (str(row.get("date", "")), str(row.get("time", "")))


def _build_latest_signal_payload(rows: List[Dict]) -> Dict:
    total_signals = len(rows)
    selected_count = 0
    latest_signal_row = None
    latest_valid_key = None
    latest_valid_batch_rows: List[Dict] = []

    for row in rows:
        latest_signal_row = row
        if row.get("selected") == "Y":
            selected_count += 1
        if not _is_valid_trade_signal(row):
            continue
        row_key = _signal_row_key(row)
        if latest_valid_key != row_key:
            latest_valid_key = row_key
            latest_valid_batch_rows = [row]
        else:
            latest_valid_batch_rows.append(row)

    deduped_batch: Dict[str, Dict] = {}
    for row in latest_valid_batch_rows:
        deduped_batch[f"{row.get('strike')}-{row.get('side')}"] = _transform_signal_row(row)

    return {
        "rows": list(deduped_batch.values()),
        "total_signals": total_signals,
        "selected_count": selected_count,
        "latest_signal": _transform_signal_row(latest_signal_row) if latest_signal_row else {},
    }


def _empty_latest_signal_payload() -> Dict:
    return {
        "rows": [],
        "total_signals": 0,
        "selected_count": 0,
        "latest_signal": {},
    }


def _build_latest_signal_payload_from_csv(file_path) -> Dict:
    from pathlib import Path
    file_path = Path(file_path)
    if not file_path.exists():
        return _empty_latest_signal_payload()
    return _build_latest_signal_payload_from_csv_cached(str(file_path), file_path.stat().st_mtime_ns)


@lru_cache(maxsize=64)
def _build_latest_signal_payload_from_csv_cached(path_str: str, mtime_ns: int) -> Dict:
    total_signals = 0
    selected_count = 0
    latest_signal_row = None
    latest_valid_key = None
    latest_valid_batch_rows: List[Dict] = []

    with open(path_str, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            row = dict(raw_row)
            total_signals += 1
            latest_signal_row = row
            if row.get("selected") == "Y":
                selected_count += 1
            if not _is_valid_trade_signal(row):
                continue
            row_key = _signal_row_key(row)
            if latest_valid_key != row_key:
                latest_valid_key = row_key
                latest_valid_batch_rows = [row]
            else:
                latest_valid_batch_rows.append(row)

    if latest_signal_row is None:
        return _empty_latest_signal_payload()

    deduped_batch: Dict[str, Dict] = {}
    for row in latest_valid_batch_rows:
        deduped_batch[f"{row.get('strike')}-{row.get('side')}"] = _transform_signal_row(row)

    return {
        "rows": list(deduped_batch.values()),
        "total_signals": total_signals,
        "selected_count": selected_count,
        "latest_signal": _transform_signal_row(latest_signal_row),
    }


# ---------------------------------------------------------------------------
# Internal loaders
# ---------------------------------------------------------------------------


def _list_fyersn7_dates() -> List[str]:
    if not FYERSN7_DATA_PATH.exists():
        return []
    return sorted([
        d.name for d in FYERSN7_DATA_PATH.iterdir()
        if d.is_dir() and d.name.startswith("202")
    ], reverse=True)


def _get_fyersn7_date_dir(date: str):
    from pathlib import Path
    date_dir = FYERSN7_DATA_PATH / date
    if not date_dir.exists():
        raise HTTPException(status_code=404, detail=f"No data for date {date}")
    return date_dir


def _get_fyersn7_indices(date_dir, index: Optional[str] = None) -> List[str]:
    return [index] if index else [d.name for d in date_dir.iterdir() if d.is_dir()]


def _load_fyersn7_signals(date_dir, indices: List[str], latest_only: bool = False) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for idx in indices:
        csv_path = date_dir / idx / "decision_journal.csv"
        if latest_only:
            result[idx] = _build_latest_signal_payload_from_csv(csv_path)
        else:
            rows = _read_csv_as_dicts(csv_path)
            result[idx] = [_transform_signal_row(row) for row in rows]
    return result


def _load_fyersn7_trades(date_dir, indices: List[str]) -> Dict[str, List[Dict]]:
    result: Dict[str, List[Dict]] = {}
    for idx in indices:
        csv_path = date_dir / idx / "paper_trades.csv"
        rows = _dedupe_trade_rows(_read_csv_as_dicts(csv_path))
        trades = []
        for row in rows:
            trade_id = row.get("trade_id", "")
            trades.append({
                "id": trade_id,
                "trade_id": trade_id,
                "side": row.get("side", ""),
                "strike": _to_int(row.get("strike")),
                "qty": _to_int(row.get("qty")),
                "entry_time": row.get("entry_time", ""),
                "entry_price": _to_float(row.get("entry_price")),
                "sl": _to_float(row.get("sl")),
                "t1": _to_float(row.get("t1")),
                "t2": _to_float(row.get("t2")),
                "exit_time": row.get("exit_time", ""),
                "exit_price": _to_float(row.get("exit_price")),
                "exit_reason": row.get("exit_reason", ""),
                "net_pnl": _to_float(row.get("net_pnl")),
                "hold_sec": _to_int(row.get("hold_sec")),
                "result": row.get("result", ""),
                "engine": row.get("engine_id") or f"fyersn7_{idx}",
                "index": row.get("index") or idx,
            })
        result[idx] = trades
    return result


def _load_fyersn7_events(date_dir, indices: List[str]) -> Dict[str, List[Dict]]:
    result: Dict[str, List[Dict]] = {}
    for idx in indices:
        csv_path = date_dir / idx / "opportunity_events.csv"
        rows = _dedupe_event_rows(_read_csv_as_dicts(csv_path))
        events = []
        for row in rows:
            events.append({
                "event_date": row.get("event_date", ""),
                "event_time": row.get("event_time", ""),
                "event_type": row.get("event_type", ""),
                "side": row.get("side", ""),
                "strike": _to_int(row.get("strike")),
                "entry": _to_float(row.get("entry")),
                "exit": _to_float(row.get("exit")),
                "score": _to_int(row.get("score")),
                "confidence": _to_int(row.get("confidence")),
                "vote_diff": _to_int(row.get("vote_diff")),
                "reason": row.get("reason", ""),
            })
        result[idx] = events
    return result


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.get("/fyers/screener/latest")
async def get_latest_fyers_screener():
    """Get the most recent FYERS screener output JSON."""
    if not FYERS_DATA_PATH.exists():
        failure = _latest_fyers_screener_failure()
        if failure:
            return {"available": False, **failure}
        return {"available": False, "message": "No FYERS screener data directory found"}

    screener_files = sorted(
        FYERS_DATA_PATH.glob("screener_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not screener_files:
        failure = _latest_fyers_screener_failure()
        if failure:
            return {"available": False, **failure}
        return {"available": False, "message": "No screener runs found"}

    latest_file = screener_files[0]
    try:
        payload = json.loads(latest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"Invalid JSON in {latest_file.name}")

    return {
        "available": True,
        "file": latest_file.name,
        "updated_at": datetime.fromtimestamp(latest_file.stat().st_mtime).isoformat(),
        "data": payload,
    }


@router.get("/fyersn7/dates")
async def get_fyersn7_dates():
    """Get list of available dates with signal data."""
    if not FYERSN7_DATA_PATH.exists():
        return {"dates": [], "message": "No fyersN7 data directory found"}
    dates = _list_fyersn7_dates()
    return {"dates": dates, "latest": dates[0] if dates else None}


@router.get("/fyersn7/signals/{date}")
async def get_fyersn7_signals(date: str, index: Optional[str] = None, latest_only: bool = Query(False)):
    """Get decision journal signals for a date (all indices or specific)."""
    date_dir = _get_fyersn7_date_dir(date)
    indices = _get_fyersn7_indices(date_dir, index)
    return {"date": date, "indices": _load_fyersn7_signals(date_dir, indices, latest_only=latest_only)}


@router.get("/fyersn7/trades/{date}")
async def get_fyersn7_trades(date: str, index: Optional[str] = None):
    """Get paper trades for a date."""
    date_dir = _get_fyersn7_date_dir(date)
    indices = _get_fyersn7_indices(date_dir, index)
    return {"date": date, "indices": _load_fyersn7_trades(date_dir, indices)}


@router.get("/fyersn7/trades-flat/{date}")
async def get_fyersn7_trades_flat(date: str):
    """FyersN7 closed trades across all indices, flattened to AutoTrader-compatible shape."""
    date_dir = _get_fyersn7_date_dir(date)
    indices = _get_fyersn7_indices(date_dir)
    by_index = _load_fyersn7_trades(date_dir, indices)

    trades = []
    for idx, rows in by_index.items():
        for t in rows:
            entry_ts = f"{date}T{t['entry_time']}" if t.get("entry_time") else None
            trades.append({
                "id":           f"{idx}_{t['trade_id']}",
                "trade_id":     t["trade_id"],
                "index":        t.get("index") or idx,
                "option_type":  t.get("side"),
                "strike":       t.get("strike"),
                "entry_price":  t.get("entry_price"),
                "exit_price":   t.get("exit_price"),
                "pnl":          t.get("net_pnl"),
                "outcome":      t["result"].upper() if t.get("result") else None,
                "exit_reason":  t.get("exit_reason"),
                "timestamp":    entry_ts,
                "entry_time":   entry_ts,
                "mode":         "paper",
                "engine":       t.get("engine", "fyersn7"),
                "qty":          t.get("qty"),
                "bot_signals":  None,
            })

    trades.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return {"trades": trades, "count": len(trades), "timestamp": datetime.now().isoformat()}


@router.get("/fyersn7/events/{date}")
async def get_fyersn7_events(date: str, index: Optional[str] = None):
    """Get opportunity events (entries/exits) for a date."""
    date_dir = _get_fyersn7_date_dir(date)
    indices = _get_fyersn7_indices(date_dir, index)
    return {"date": date, "indices": _load_fyersn7_events(date_dir, indices)}


@router.get("/fyersn7/snapshot/{date}")
async def get_fyersn7_snapshot(date: str, index: Optional[str] = None, latest_only: bool = Query(False)):
    """Get a combined SignalView payload in a single request."""
    date_dir = _get_fyersn7_date_dir(date)
    indices = _get_fyersn7_indices(date_dir, index)
    return {
        "date": date,
        "signals": _load_fyersn7_signals(date_dir, indices, latest_only=latest_only),
        "trades": _load_fyersn7_trades(date_dir, indices),
        "events": _load_fyersn7_events(date_dir, indices),
    }


@router.get("/fyersn7/live-signals/{index}")
async def get_fyersn7_live_signals(index: str):
    """Get the latest available date plus full signal rows for one index."""
    dates = _list_fyersn7_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="No fyersN7 dates found")

    latest_date = dates[0]
    date_dir = _get_fyersn7_date_dir(latest_date)
    signals = _load_fyersn7_signals(date_dir, [index], latest_only=False)

    return {
        "date": latest_date,
        "index": index,
        "rows": signals.get(index, []),
    }


@router.get("/fyersn7/summary/{date}")
async def get_fyersn7_summary(date: str):
    """Get summary statistics for all indices on a date."""
    date_dir = FYERSN7_DATA_PATH / date
    if not date_dir.exists():
        raise HTTPException(status_code=404, detail=f"No data for date {date}")

    indices = [d.name for d in date_dir.iterdir() if d.is_dir()]
    summaries = {}

    for idx in indices:
        trades_path = date_dir / idx / "paper_trades.csv"
        trades = _dedupe_trade_rows(_read_csv_as_dicts(trades_path))

        total_pnl = sum(_to_float(t.get("net_pnl")) for t in trades)
        wins = sum(1 for t in trades if t.get("result") == "Win")
        losses = sum(1 for t in trades if t.get("result") == "Loss")

        signals_path = date_dir / idx / "decision_journal.csv"
        signals_meta = _build_latest_signal_payload_from_csv(signals_path)
        latest_signal = signals_meta.get("latest_signal", {})

        summaries[idx] = {
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(trades) * 100, 1) if trades else 0,
            "total_pnl": round(total_pnl, 2),
            "total_entries": signals_meta.get("selected_count", 0),
            "total_signals": signals_meta.get("total_signals", 0),
            "spot": _to_float(latest_signal.get("spot")),
            "vix": _to_float(latest_signal.get("vix")),
            "net_pcr": _to_float(latest_signal.get("net_pcr")),
        }

    return {"date": date, "summaries": summaries}
