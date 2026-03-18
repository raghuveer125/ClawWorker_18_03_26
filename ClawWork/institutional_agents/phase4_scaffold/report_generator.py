from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _table(rows: List[Dict[str, Any]], max_rows: int = 20) -> str:
    lines = [
        "| # | Underlying | Session | Regime | Event Risk | Action | Confidence | Size Mult |",
        "|---|------------|---------|--------|------------|--------|------------|-----------|",
    ]
    for idx, row in enumerate(rows[:max_rows], start=1):
        inp = row.get("input", {})
        dec = row.get("decision", {})
        tags = dec.get("policy_tags", {})
        lines.append(
            "| {idx} | {u} | {s} | {r} | {e} | {a} | {c} | {m} |".format(
                idx=idx,
                u=inp.get("underlying", ""),
                s=inp.get("session_slot", ""),
                r=tags.get("regime", ""),
                e=tags.get("event_risk", ""),
                a=dec.get("action", ""),
                c=dec.get("confidence", ""),
                m=dec.get("position_size_multiplier", ""),
            )
        )
    return "\n".join(lines)


def _render(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {})
    counts = summary.get("action_counts", {})
    rows = report.get("results", [])

    lines = [
        f"# Phase 4 Report ({report.get('run_tag', 'phase4_demo')})",
        "",
        "## Summary",
        f"- Total records: {summary.get('total_records', 0)}",
        f"- BUY_CALL: {counts.get('BUY_CALL', 0)}",
        f"- BUY_PUT: {counts.get('BUY_PUT', 0)}",
        f"- NO_TRADE: {counts.get('NO_TRADE', 0)}",
        f"- Event blocks: {summary.get('event_block_count', 0)} ({summary.get('event_block_pct', 0.0)}%)",
        f"- Portfolio blocks: {summary.get('portfolio_block_count', 0)} ({summary.get('portfolio_block_pct', 0.0)}%)",
        "",
        "## Decisions",
        _table(rows),
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Phase 4 markdown report")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    report_path = Path(args.report_json)
    report = _load(report_path)
    out_md = Path(args.out_md) if args.out_md else report_path.with_suffix(".md")
    out_md.write_text(_render(report), encoding="utf-8")

    print(json.dumps({"markdown_report": str(out_md)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
