from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_action_table(decisions: List[Dict[str, Any]], max_rows: int = 20) -> str:
    lines = [
        "| # | Underlying | Action | Confidence | Strike | Rationale |",
        "|---|------------|--------|------------|--------|-----------|",
    ]
    for index, row in enumerate(decisions[:max_rows], start=1):
        underlying = row.get("underlying", "")
        action = row.get("action", "")
        confidence = row.get("confidence", "")
        strike = row.get("preferred_strike", "")
        rationale = str(row.get("rationale", "")).replace("|", "/")
        lines.append(
            f"| {index} | {underlying} | {action} | {confidence} | {strike} | {rationale} |"
        )

    if len(decisions) > max_rows:
        lines.append("")
        lines.append(f"_Showing first {max_rows} of {len(decisions)} decisions._")

    return "\n".join(lines)


def _render_markdown(payload: Dict[str, Any]) -> str:
    run_tag = payload.get("run_tag", "run")
    input_file = payload.get("input_file", "")
    record_count = payload.get("record_count", 0)
    summary = payload.get("summary", {})
    action_counts = summary.get("action_counts", {})
    action_distribution_pct = summary.get("action_distribution_pct", {})
    confidence_counts = summary.get("confidence_counts", {})
    risk_veto_count = summary.get("risk_veto_count", 0)
    risk_veto_pct = summary.get("risk_veto_pct", 0.0)
    decisions = payload.get("decisions", [])

    sections = [
        f"# Phase 1 Paper Backtest Report ({run_tag})",
        "",
        "## Run Metadata",
        f"- Input file: {input_file}",
        f"- Record count: {record_count}",
        "",
        "## Action Summary",
        f"- BUY_CALL: {action_counts.get('BUY_CALL', 0)} ({action_distribution_pct.get('BUY_CALL', 0.0)}%)",
        f"- BUY_PUT: {action_counts.get('BUY_PUT', 0)} ({action_distribution_pct.get('BUY_PUT', 0.0)}%)",
        f"- NO_TRADE: {action_counts.get('NO_TRADE', 0)} ({action_distribution_pct.get('NO_TRADE', 0.0)}%)",
        "",
        "## Confidence Summary",
        f"- HIGH: {confidence_counts.get('HIGH', 0)}",
        f"- MEDIUM: {confidence_counts.get('MEDIUM', 0)}",
        f"- LOW: {confidence_counts.get('LOW', 0)}",
        "",
        "## Risk Guard Summary",
        f"- Risk veto count: {risk_veto_count}",
        f"- Risk veto %: {risk_veto_pct}%",
        "",
        "## Decision Sample",
        _format_action_table(decisions),
        "",
        "## Analyst Notes",
        "- What worked:",
        "- What failed/edge cases:",
        "- Recommended threshold updates:",
        "",
        "## Sign-off",
        "- Product:",
        "- Risk:",
        "- Engineering:",
    ]
    return "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate markdown report from phase1 batch output")
    parser.add_argument("--report-json", required=True, help="Path to *_report.json produced by batch_runner.py")
    parser.add_argument("--out-md", default=None, help="Output markdown path (default: alongside report json)")
    args = parser.parse_args()

    report_path = Path(args.report_json)
    payload = _load_json(report_path)

    out_md = Path(args.out_md) if args.out_md else report_path.with_suffix(".md")
    markdown = _render_markdown(payload)
    out_md.write_text(markdown, encoding="utf-8")

    print(json.dumps({"markdown_report": str(out_md)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
