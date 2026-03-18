from __future__ import annotations

from typing import Any, Dict, List


def _status(ok: bool) -> str:
    return "OK" if ok else "ALERT"


def _severity(metric_name: str, value: float, threshold: float) -> str:
    gap = value - threshold
    if metric_name == "no_trade_pct":
        gap = abs(gap)
    if abs(gap) >= 20:
        return "HIGH"
    if abs(gap) >= 10:
        return "MEDIUM"
    return "LOW"


class MonitoringDashboard:
    def __init__(self, rules: Dict[str, float]):
        self.rules = rules

    def build_snapshot(self, report: Dict[str, Any], release_ok: bool, quality_ok: bool) -> Dict[str, Any]:
        summary = report.get("summary", {})
        total_records = float(summary.get("total_records", 0) or 0)
        action_counts = summary.get("action_counts", {})

        buy_call = float(action_counts.get("BUY_CALL", 0) or 0)
        buy_put = float(action_counts.get("BUY_PUT", 0) or 0)
        no_trade = float(action_counts.get("NO_TRADE", 0) or 0)

        directional = buy_call + buy_put
        directional_pct = (100.0 * directional / total_records) if total_records else 0.0
        no_trade_pct = (100.0 * no_trade / total_records) if total_records else 0.0

        event_block_pct = float(summary.get("event_block_pct", 0.0) or 0.0)
        portfolio_block_pct = float(summary.get("portfolio_block_pct", 0.0) or 0.0)

        return {
            "run_tag": report.get("run_tag", "phase4"),
            "records": int(total_records),
            "kpis": {
                "directional_pct": round(directional_pct, 2),
                "no_trade_pct": round(no_trade_pct, 2),
                "event_block_pct": round(event_block_pct, 2),
                "portfolio_block_pct": round(portfolio_block_pct, 2),
                "release_check_status": _status(release_ok),
                "quality_check_status": _status(quality_ok),
            },
        }

    def evaluate_alerts(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        kpis = snapshot.get("kpis", {})
        alerts: List[Dict[str, Any]] = []

        comparisons = [
            ("event_block_pct", float(kpis.get("event_block_pct", 0.0)), self.rules.get("max_event_block_pct", 50.0), "GT"),
            ("portfolio_block_pct", float(kpis.get("portfolio_block_pct", 0.0)), self.rules.get("max_portfolio_block_pct", 35.0), "GT"),
            ("no_trade_pct", float(kpis.get("no_trade_pct", 0.0)), self.rules.get("max_no_trade_pct", 70.0), "GT"),
            ("directional_pct", float(kpis.get("directional_pct", 0.0)), self.rules.get("min_directional_pct", 20.0), "LT"),
        ]

        for metric, value, threshold, op in comparisons:
            breached = value > threshold if op == "GT" else value < threshold
            if breached:
                alerts.append(
                    {
                        "metric": metric,
                        "value": round(value, 2),
                        "threshold": threshold,
                        "operator": op,
                        "severity": _severity(metric, value, threshold),
                        "message": f"{metric} breached threshold ({value} vs {threshold}).",
                    }
                )

        if kpis.get("release_check_status") != "OK":
            alerts.append(
                {
                    "metric": "release_check_status",
                    "value": kpis.get("release_check_status"),
                    "threshold": "OK",
                    "operator": "EQ",
                    "severity": "HIGH",
                    "message": "Release check is not OK.",
                }
            )

        if kpis.get("quality_check_status") != "OK":
            alerts.append(
                {
                    "metric": "quality_check_status",
                    "value": kpis.get("quality_check_status"),
                    "threshold": "OK",
                    "operator": "EQ",
                    "severity": "HIGH",
                    "message": "Quality check is not OK.",
                }
            )

        return alerts
