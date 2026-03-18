from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _confidence_label(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0

    if score >= 70:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    return "LOW"


def _baseline_signal_to_action(signal: str) -> str:
    mapped = str(signal or "").upper()
    if mapped == "BULLISH":
        return "BUY_CALL"
    if mapped == "BEARISH":
        return "BUY_PUT"
    return "NO_TRADE"


def _institutional_from_index_row(row: Dict[str, Any]) -> Dict[str, Any]:
    signal = str(row.get("signal", "NEUTRAL")).upper()
    change_pct = row.get("change_pct", 0.0)
    try:
        change_abs = abs(float(change_pct))
    except (TypeError, ValueError):
        change_abs = 0.0

    baseline_action = _baseline_signal_to_action(signal)
    weighted_score = round(min(100.0, change_abs * 100.0), 2)

    if baseline_action == "NO_TRADE":
        institutional_action = "NO_TRADE"
    else:
        institutional_action = baseline_action

    confidence = _confidence_label(row.get("confidence", weighted_score))
    rationale = str(row.get("reason", "")) or "Index recommendation mapped to institutional shadow action."

    return {
        "institutional_action": institutional_action,
        "institutional_confidence": confidence,
        "institutional_weighted_score": weighted_score,
        "institutional_rationale": rationale,
        "veto_applied": False,
    }


def _build_shadow_records(baseline_result: Dict[str, Any], timestamp_iso: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    index_rows = baseline_result.get("index_recommendations", [])

    for row in index_rows:
        if not isinstance(row, dict):
            continue

        underlying = str(row.get("index", "")).upper()
        baseline_signal = str(row.get("signal", "NEUTRAL")).upper()
        baseline_action = _baseline_signal_to_action(baseline_signal)
        baseline_confidence = _confidence_label(row.get("confidence", 0))

        institutional = _institutional_from_index_row(row)
        institutional_action = institutional.get("institutional_action", "NO_TRADE")

        comparison = "agree" if institutional_action == baseline_action else "disagree"

        records.append(
            {
                "timestamp": timestamp_iso,
                "underlying": underlying,
                "baseline_signal": baseline_signal,
                "baseline_action": baseline_action,
                "baseline_confidence": baseline_confidence,
                "institutional_action": institutional_action,
                "institutional_confidence": institutional.get("institutional_confidence"),
                "institutional_weighted_score": institutional.get("institutional_weighted_score"),
                "institutional_rationale": institutional.get("institutional_rationale"),
                "veto_applied": bool(institutional.get("veto_applied", False)),
                "comparison_label": comparison,
            }
        )

    return records


def _shadow_log_path(data_path: str | None, signature: str | None) -> str | None:
    if not data_path:
        return None

    trading_dir = os.path.join(data_path, "trading")
    if signature and os.path.basename(os.path.normpath(data_path)) != signature:
        trading_dir = os.path.join(data_path, signature, "trading")

    os.makedirs(trading_dir, exist_ok=True)
    return os.path.join(trading_dir, "institutional_shadow.jsonl")


def _append_shadow_log(path: str | None, payload: Dict[str, Any]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_shadow_adapter(
    baseline_result: Dict[str, Any],
    runtime_context: Dict[str, Any],
) -> Dict[str, Any]:
    enabled = _env_flag("INSTITUTIONAL_ADAPTER_ENABLED", False)
    shadow_mode = _env_flag("INSTITUTIONAL_SHADOW_MODE", True)

    if not enabled:
        return {
            "status": "disabled",
            "enabled": False,
            "shadow_mode": shadow_mode,
            "record_count": 0,
        }

    if not shadow_mode:
        return {
            "status": "disabled_shadow_mode_off",
            "enabled": True,
            "shadow_mode": False,
            "record_count": 0,
        }

    timestamp_iso = datetime.now().isoformat()
    records = _build_shadow_records(baseline_result, timestamp_iso=timestamp_iso)

    agrees = sum(1 for item in records if item.get("comparison_label") == "agree")
    disagrees = sum(1 for item in records if item.get("comparison_label") == "disagree")

    log_envelope = {
        "timestamp": timestamp_iso,
        "date": runtime_context.get("current_date"),
        "signature": runtime_context.get("signature"),
        "record_count": len(records),
        "agree_count": agrees,
        "disagree_count": disagrees,
        "records": records,
    }

    log_path = _shadow_log_path(
        data_path=runtime_context.get("data_path"),
        signature=runtime_context.get("signature"),
    )
    _append_shadow_log(log_path, log_envelope)

    return {
        "status": "ok",
        "enabled": True,
        "shadow_mode": True,
        "record_count": len(records),
        "agree_count": agrees,
        "disagree_count": disagrees,
        "log_path": log_path,
        "records": records,
    }
