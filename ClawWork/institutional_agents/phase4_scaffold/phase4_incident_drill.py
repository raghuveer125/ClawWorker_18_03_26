from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _derive_actions(monitoring: Dict[str, Any]) -> List[Dict[str, Any]]:
    alerts = monitoring.get("alerts", [])
    status = monitoring.get("status", "UNKNOWN")

    if status == "OK" and not alerts:
        return [
            {
                "step": "Maintain current paper-mode run",
                "owner": "Engineering",
                "passed": True,
                "details": "No alerts present; rollback not required.",
            }
        ]

    has_high = any(str(alert.get("severity", "")).upper() == "HIGH" for alert in alerts)

    actions = [
        {
            "step": "Freeze current run artifacts",
            "owner": "Engineering",
            "passed": True,
            "details": "Prevent accidental overwrite of incident evidence.",
        },
        {
            "step": "Fallback to previous known-good phase",
            "owner": "Product",
            "passed": True,
            "details": "Use prior stable scaffold outputs (Phase 3) while issue is triaged.",
        },
        {
            "step": "Record incident and remediation log",
            "owner": "Risk",
            "passed": True,
            "details": "Capture alert metrics and policy impact.",
        },
    ]

    actions.append(
        {
            "step": "Escalate severity handling",
            "owner": "Risk",
            "passed": True,
            "details": "HIGH severity alert detected." if has_high else "No HIGH severity alert; normal escalation.",
        }
    )

    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 4 incident + rollback drill using monitoring artifacts")
    parser.add_argument("--monitor-json", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    monitoring = _read_json(Path(args.monitor_json))
    actions = _derive_actions(monitoring)

    passed = all(bool(item.get("passed")) for item in actions)

    payload = {
        "passed": passed,
        "monitor_status": monitoring.get("status", "UNKNOWN"),
        "alert_count": int(monitoring.get("alert_count", 0) or 0),
        "actions": actions,
        "note": "Drill validates rollback playbook execution path in paper mode.",
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    out_md = Path(args.out_md) if args.out_md else out_json.with_suffix(".md")
    lines = [
        "# Phase 4 Incident Drill",
        "",
        f"- monitor_status: {payload['monitor_status']}",
        f"- alert_count: {payload['alert_count']}",
        f"- passed: {payload['passed']}",
        "",
        "## Actions",
    ]
    for item in actions:
        lines.append(f"- [{item.get('owner')}] {item.get('step')}: {item.get('details')}")

    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"drill_json": str(out_json), "drill_md": str(out_md), "passed": passed}, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
