from __future__ import annotations

from typing import Any, Dict, List


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def summarize_decisions(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    buy_call = sum(1 for row in rows if row.get("action") == "BUY_CALL")
    buy_put = sum(1 for row in rows if row.get("action") == "BUY_PUT")
    no_trade = sum(1 for row in rows if row.get("action") == "NO_TRADE")

    high_conf = sum(1 for row in rows if row.get("confidence") == "HIGH")
    med_conf = sum(1 for row in rows if row.get("confidence") == "MEDIUM")
    low_conf = sum(1 for row in rows if row.get("confidence") == "LOW")

    veto_count = sum(
        1
        for row in rows
        if any(
            value == "FAIL"
            for value in (row.get("risk_checks") or {}).values()
        )
    )

    return {
        "total_decisions": total,
        "action_counts": {
            "BUY_CALL": buy_call,
            "BUY_PUT": buy_put,
            "NO_TRADE": no_trade,
        },
        "action_distribution_pct": {
            "BUY_CALL": round(_safe_div(buy_call * 100.0, total), 2),
            "BUY_PUT": round(_safe_div(buy_put * 100.0, total), 2),
            "NO_TRADE": round(_safe_div(no_trade * 100.0, total), 2),
        },
        "confidence_counts": {
            "HIGH": high_conf,
            "MEDIUM": med_conf,
            "LOW": low_conf,
        },
        "risk_veto_count": veto_count,
        "risk_veto_pct": round(_safe_div(veto_count * 100.0, total), 2),
    }
