from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _table(rows: List[Dict[str, Any]], max_rows: int = 20) -> str:
    lines = [
        "| # | Underlying | Market | Options | Risk Veto | Consensus | Confidence |",
        "|---|------------|--------|---------|-----------|-----------|------------|",
    ]
    for idx, row in enumerate(rows[:max_rows], start=1):
        lines.append(
            "| {idx} | {u} | {m} | {o} | {v} | {c} | {conf} |".format(
                idx=idx,
                u=row.get("underlying", ""),
                m=row.get("market_regime", {}).get("action", ""),
                o=row.get("options_structure", {}).get("action", ""),
                v=row.get("consensus", {}).get("veto_applied", False),
                c=row.get("consensus", {}).get("action", ""),
                conf=row.get("consensus", {}).get("confidence", ""),
            )
        )
    return "\n".join(lines)


def _render(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {})
    counts = summary.get("consensus_counts", {})
    rows = report.get("results", [])

    lines = [
        f"# Phase 3 Report ({report.get('run_tag', 'phase3_demo')})",
        "",
        "## Run Metadata",
        f"- Input file: {report.get('input_file', '')}",
        f"- Record count: {report.get('record_count', 0)}",
        "",
        "## Consensus Summary",
        f"- BUY_CALL: {counts.get('BUY_CALL', 0)}",
        f"- BUY_PUT: {counts.get('BUY_PUT', 0)}",
        f"- NO_TRADE: {counts.get('NO_TRADE', 0)}",
        f"- Veto count: {summary.get('veto_count', 0)}",
        f"- Veto %: {summary.get('veto_pct', 0.0)}",
        "",
        "## Decision Samples",
        _table(rows),
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Phase 3 markdown report")
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
