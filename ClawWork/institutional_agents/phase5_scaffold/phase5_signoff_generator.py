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
    parser = argparse.ArgumentParser(description="Generate Phase 5 sign-off note")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    report = _read_json(Path(args.report_json))
    comp = report.get("shadow_comparison", {})
    gate = report.get("go_live_gate", {})

    lines = [
        f"# Phase 5 Sign-off Note ({report.get('run_tag', 'phase5')})",
        "",
        f"Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        "## Shadow Comparison",
        f"- Total rows: {comp.get('total_rows', 0)}",
        f"- Agreement %: {_fmt(comp.get('agreement_pct'))}",
        f"- Institutional better: {comp.get('institutional_better_count', 0)}",
        f"- Baseline better: {comp.get('baseline_better_count', 0)}",
        "",
        "## Go-live Gate",
        f"- Passed: {gate.get('passed', False)}",
        f"- Failed reasons: {', '.join(gate.get('reasons', [])) if gate.get('reasons') else 'None'}",
        "",
        "## Signatures",
        "- Product Owner:",
        "- Risk Owner:",
        "- Engineering Owner:",
    ]

    out = Path(args.out_md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"signoff_markdown": str(out), "passed": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
