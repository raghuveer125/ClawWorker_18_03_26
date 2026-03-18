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
    parser = argparse.ArgumentParser(description="Generate Phase 3 sign-off note")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    report_path = outdir / f"{args.tag}_report.json"
    summary_path = outdir / f"{args.tag}_summary.json"

    if not report_path.exists() or not summary_path.exists():
        payload = {
            "signoff_markdown": None,
            "passed": False,
            "reason": "Required report/summary artifacts not found.",
        }
        print(json.dumps(payload, indent=2))
        return 2

    summary = _read_json(summary_path)
    counts = summary.get("consensus_counts", {})

    lines = [
        f"# Phase 3 Sign-off Note ({args.tag})",
        "",
        f"Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        "## Run Summary",
        f"- Records: {summary.get('total_records', 0)}",
        f"- BUY_CALL: {counts.get('BUY_CALL', 0)}",
        f"- BUY_PUT: {counts.get('BUY_PUT', 0)}",
        f"- NO_TRADE: {counts.get('NO_TRADE', 0)}",
        f"- Veto count: {summary.get('veto_count', 0)}",
        f"- Veto %: {_fmt(summary.get('veto_pct'))}%",
        "",
        "## Recommendation",
        "- Proceed with stress-scenario simulation and policy-violation testing.",
        "",
        "## Signatures",
        "- Product Owner:",
        "- Risk Owner:",
        "- Engineering Owner:",
    ]

    out_md = Path(args.out_md) if args.out_md else outdir / f"{args.tag}_phase3_signoff.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"signoff_markdown": str(out_md), "passed": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
