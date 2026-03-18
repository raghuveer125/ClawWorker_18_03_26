from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _table(rows: List[Dict[str, Any]], max_rows: int = 20) -> str:
    lines = [
        "| # | Underlying | Momentum | Options | Final | Opt Score | Rationale |",
        "|---|------------|----------|---------|-------|-----------|-----------|",
    ]
    for index, row in enumerate(rows[:max_rows], start=1):
        underlying = row.get("underlying", "")
        momentum = row.get("momentum_signal", {}).get("action", "")
        options_signal = row.get("options_signal", {}).get("signal", "")
        final_action = row.get("final_decision", {}).get("action", "")
        opt_score = row.get("options_signal", {}).get("options_score", "")
        rationale = str(row.get("final_decision", {}).get("rationale", "")).replace("|", "/")
        lines.append(
            f"| {index} | {underlying} | {momentum} | {options_signal} | {final_action} | {opt_score} | {rationale} |"
        )

    if len(rows) > max_rows:
        lines.append("")
        lines.append(f"_Showing first {max_rows} of {len(rows)} rows._")

    return "\n".join(lines)


def _render(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {})
    counts = summary.get("final_action_counts", {})
    options_counts = summary.get("options_signal_counts", {})
    rows = report.get("results", [])

    lines = [
        f"# Phase 2 Report ({report.get('run_tag', 'phase2')})",
        "",
        "## Run Metadata",
        f"- Input file: {report.get('input_file', '')}",
        f"- Record count: {report.get('record_count', 0)}",
        "",
        "## Final Action Summary",
        f"- BUY_CALL: {counts.get('BUY_CALL', 0)}",
        f"- BUY_PUT: {counts.get('BUY_PUT', 0)}",
        f"- NO_TRADE: {counts.get('NO_TRADE', 0)}",
        "",
        "## Options Signal Summary",
        f"- BULLISH: {options_counts.get('BULLISH', 0)}",
        f"- BEARISH: {options_counts.get('BEARISH', 0)}",
        f"- NEUTRAL: {options_counts.get('NEUTRAL', 0)}",
        f"- NO_TRADE (veto): {options_counts.get('NO_TRADE', 0)}",
        f"- Options veto %: {summary.get('options_veto_pct', 0.0)}%",
        f"- Average options score: {summary.get('average_options_score', 0.0)}",
        "",
        "## Decision Sample",
        _table(rows),
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Phase 2 markdown report from batch JSON output")
    parser.add_argument("--report-json", required=True, help="Path to report json")
    parser.add_argument("--out-md", default=None, help="Output markdown path")
    args = parser.parse_args()

    report_path = Path(args.report_json)
    report = _load_json(report_path)
    markdown = _render(report)

    out_md = Path(args.out_md) if args.out_md else report_path.with_suffix(".md")
    out_md.write_text(markdown, encoding="utf-8")

    print(json.dumps({"markdown_report": str(out_md)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
