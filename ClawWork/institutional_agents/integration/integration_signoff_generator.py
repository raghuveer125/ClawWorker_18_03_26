from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate integration Step 4 signoff note")
    parser.add_argument("--gate-json", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    gate = _read_json(Path(args.gate_json))
    checks = gate.get("checks", {})
    metrics = gate.get("metrics", {})

    lines = [
        "# Integration Step 4 Sign-off Note",
        "",
        f"Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        "## Gate Summary",
        f"- Overall passed: {gate.get('passed', False)}",
        f"- Failed reasons: {', '.join(gate.get('reasons', [])) if gate.get('reasons') else 'None'}",
        "",
        "## Check Status",
        f"- Performance gate met: {checks.get('performance_gate_met', False)}",
        f"- Risk gate met: {checks.get('risk_gate_met', False)}",
        f"- Reliability gate met: {checks.get('reliability_gate_met', False)}",
        f"- Rollback gate met: {checks.get('rollback_gate_met', False)}",
        f"- Shadow window min sessions met: {checks.get('shadow_window_min_sessions_met', False)}",
        "",
        "## Key Metrics",
        f"- Session count: {metrics.get('session_count', 0)} / min {_fmt(metrics.get('min_sessions', 0), 0)}",
        f"- Agree %: {_fmt(metrics.get('agree_pct'))} / min {_fmt(metrics.get('min_agree_pct'))}",
        f"- Disagree %: {_fmt(metrics.get('disagree_pct'))} / max {_fmt(metrics.get('max_disagree_pct'))}",
        f"- Observability status: {metrics.get('observability_status')}",
        f"- Alert count: {metrics.get('alert_count')}",
        f"- Fallback verified: {metrics.get('fallback_verified')}",
        f"- Rollback test passed: {metrics.get('rollback_test_passed')}",
        "",
        "## Signatures",
        "- Product Owner:",
        "- Risk Owner:",
        "- Engineering Owner:",
    ]

    out_path = Path(args.out_md)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"signoff_markdown": str(out_path), "generated": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
