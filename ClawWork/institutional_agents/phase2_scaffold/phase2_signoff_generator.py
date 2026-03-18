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
    parser = argparse.ArgumentParser(description="Generate Phase 2 signoff note")
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

    report = _read_json(report_path)
    summary = _read_json(summary_path)

    final_counts = summary.get("final_action_counts", {})
    option_counts = summary.get("options_signal_counts", {})

    lines = [
        f"# Phase 2 Sign-off Note ({args.tag})",
        "",
        f"Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        "## Run Summary",
        f"- Records: {summary.get('record_count', 0)}",
        f"- BUY_CALL: {final_counts.get('BUY_CALL', 0)}",
        f"- BUY_PUT: {final_counts.get('BUY_PUT', 0)}",
        f"- NO_TRADE: {final_counts.get('NO_TRADE', 0)}",
        "",
        "## Options Signal Summary",
        f"- BULLISH: {option_counts.get('BULLISH', 0)}",
        f"- BEARISH: {option_counts.get('BEARISH', 0)}",
        f"- NEUTRAL: {option_counts.get('NEUTRAL', 0)}",
        f"- NO_TRADE (veto): {option_counts.get('NO_TRADE', 0)}",
        f"- Options veto %: {_fmt(summary.get('options_veto_pct'))}%",
        f"- Average options score: {_fmt(summary.get('average_options_score'))}",
        "",
        "## Recommendation",
        "- Proceed with Phase 2 checklist validation and baseline comparison against Phase 1.",
        "",
        "## Signatures",
        "- Product Owner:",
        "- Risk Owner:",
        "- Engineering Owner:",
    ]

    out_md = Path(args.out_md) if args.out_md else outdir / f"{args.tag}_phase2_signoff.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"signoff_markdown": str(out_md), "passed": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
