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
    parser = argparse.ArgumentParser(description="Generate Phase 4 sign-off note")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    summary_path = outdir / f"{args.tag}_summary.json"
    if not summary_path.exists():
        print(json.dumps({"signoff_markdown": None, "passed": False, "reason": "Missing summary artifact"}, indent=2))
        return 2

    summary = _read_json(summary_path)
    counts = summary.get("action_counts", {})

    lines = [
        f"# Phase 4 Sign-off Note ({args.tag})",
        "",
        f"Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        "## Run Summary",
        f"- Records: {summary.get('total_records', 0)}",
        f"- BUY_CALL: {counts.get('BUY_CALL', 0)}",
        f"- BUY_PUT: {counts.get('BUY_PUT', 0)}",
        f"- NO_TRADE: {counts.get('NO_TRADE', 0)}",
        f"- Event blocks: {summary.get('event_block_count', 0)} ({_fmt(summary.get('event_block_pct'))}%)",
        f"- Portfolio blocks: {summary.get('portfolio_block_count', 0)} ({_fmt(summary.get('portfolio_block_pct'))}%)",
        "",
        "## Recommendation",
        "- Proceed with position-sizing and portfolio-control simulations before integration.",
        "",
        "## Signatures",
        "- Product Owner:",
        "- Risk Owner:",
        "- Engineering Owner:",
    ]

    out_md = Path(args.out_md) if args.out_md else outdir / f"{args.tag}_phase4_signoff.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"signoff_markdown": str(out_md), "passed": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
