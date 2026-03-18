from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from monitoring import MonitoringDashboard


DEFAULT_RULES = {
    "max_event_block_pct": 50.0,
    "max_portfolio_block_pct": 35.0,
    "max_no_trade_pct": 70.0,
    "min_directional_pct": 20.0,
}


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rules(path: Path | None) -> Dict[str, float]:
    if path is None:
        return dict(DEFAULT_RULES)
    payload = _read_json(path)
    merged = dict(DEFAULT_RULES)
    for key, value in payload.items():
        try:
            merged[key] = float(value)
        except (TypeError, ValueError):
            continue
    return merged


def _render_markdown(payload: Dict[str, Any]) -> str:
    snapshot = payload.get("snapshot", {})
    kpis = snapshot.get("kpis", {})
    alerts = payload.get("alerts", [])

    lines = [
        f"# Phase 4 Monitoring Snapshot ({snapshot.get('run_tag', 'phase4')})",
        "",
        "## KPI Summary",
        f"- directional_pct: {kpis.get('directional_pct')}",
        f"- no_trade_pct: {kpis.get('no_trade_pct')}",
        f"- event_block_pct: {kpis.get('event_block_pct')}",
        f"- portfolio_block_pct: {kpis.get('portfolio_block_pct')}",
        f"- release_check_status: {kpis.get('release_check_status')}",
        f"- quality_check_status: {kpis.get('quality_check_status')}",
        "",
        "## Alerts",
    ]

    if not alerts:
        lines.append("- No alerts triggered.")
    else:
        for alert in alerts:
            lines.append(
                f"- [{alert.get('severity')}] {alert.get('metric')}: {alert.get('message')}"
            )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Phase 4 monitoring dashboard + alerts")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--release-json", default=None)
    parser.add_argument("--quality-json", default=None)
    parser.add_argument("--rules-json", default=None)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    report = _read_json(Path(args.report_json))

    release_ok = True
    if args.release_json:
        release_ok = bool(_read_json(Path(args.release_json)).get("passed", False))

    quality_ok = True
    if args.quality_json:
        quality_ok = bool(_read_json(Path(args.quality_json)).get("passed", False))

    rules = _load_rules(Path(args.rules_json) if args.rules_json else None)
    dashboard = MonitoringDashboard(rules)

    snapshot = dashboard.build_snapshot(report=report, release_ok=release_ok, quality_ok=quality_ok)
    alerts = dashboard.evaluate_alerts(snapshot)

    payload = {
        "snapshot": snapshot,
        "alerts": alerts,
        "alert_count": len(alerts),
        "status": "ALERT" if alerts else "OK",
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    out_md = Path(args.out_md) if args.out_md else out_json.with_suffix(".md")
    out_md.write_text(_render_markdown(payload), encoding="utf-8")

    print(json.dumps({"monitor_json": str(out_json), "monitor_md": str(out_md), "status": payload["status"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
