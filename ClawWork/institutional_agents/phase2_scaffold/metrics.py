from __future__ import annotations

from typing import Any, Dict, List


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)

    buy_call = sum(1 for row in results if row.get("final_decision", {}).get("action") == "BUY_CALL")
    buy_put = sum(1 for row in results if row.get("final_decision", {}).get("action") == "BUY_PUT")
    no_trade = sum(1 for row in results if row.get("final_decision", {}).get("action") == "NO_TRADE")

    bullish_options = sum(1 for row in results if row.get("options_signal", {}).get("signal") == "BULLISH")
    bearish_options = sum(1 for row in results if row.get("options_signal", {}).get("signal") == "BEARISH")
    neutral_options = sum(1 for row in results if row.get("options_signal", {}).get("signal") == "NEUTRAL")
    veto_options = sum(1 for row in results if row.get("options_signal", {}).get("signal") == "NO_TRADE")

    avg_options_score = _safe_div(
        sum(float(row.get("options_signal", {}).get("options_score", 0.0)) for row in results),
        total,
    )

    return {
        "total_decisions": total,
        "final_action_counts": {
            "BUY_CALL": buy_call,
            "BUY_PUT": buy_put,
            "NO_TRADE": no_trade,
        },
        "final_action_distribution_pct": {
            "BUY_CALL": round(_safe_div(100.0 * buy_call, total), 2),
            "BUY_PUT": round(_safe_div(100.0 * buy_put, total), 2),
            "NO_TRADE": round(_safe_div(100.0 * no_trade, total), 2),
        },
        "options_signal_counts": {
            "BULLISH": bullish_options,
            "BEARISH": bearish_options,
            "NEUTRAL": neutral_options,
            "NO_TRADE": veto_options,
        },
        "options_veto_pct": round(_safe_div(100.0 * veto_options, total), 2),
        "average_options_score": round(avg_options_score, 2),
    }
