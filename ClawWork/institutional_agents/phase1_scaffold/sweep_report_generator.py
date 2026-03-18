from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _render_best_config(best: Dict[str, Any] | None) -> List[str]:
    if not best:
        return ["- No best config found."]

    config = best.get("config", {})
    derived = best.get("derived", {})

    return [
        f"- Bullish threshold: {_fmt_number(config.get('bullish_threshold'))}",
        f"- Bearish threshold: {_fmt_number(config.get('bearish_threshold'))}",
        f"- Strong-move threshold: {_fmt_number(config.get('strong_move_threshold'))}",
        f"- Score: {_fmt_number(derived.get('score'), 4)}",
        f"- Active decisions: {derived.get('active_count', 0)}",
        f"- Risk veto %: {_fmt_number(derived.get('veto_pct'))}%",
    ]


def _render_top_table(rows: List[Dict[str, Any]]) -> str:
    lines = [
        "| Rank | Bullish | Bearish | Strong | Score | Active | Veto % |",
        "|------|---------|---------|--------|-------|--------|--------|",
    ]

    for index, row in enumerate(rows, start=1):
        config = row.get("config", {})
        derived = row.get("derived", {})
        lines.append(
            "| "
            f"{index} | "
            f"{_fmt_number(config.get('bullish_threshold'))} | "
            f"{_fmt_number(config.get('bearish_threshold'))} | "
            f"{_fmt_number(config.get('strong_move_threshold'))} | "
            f"{_fmt_number(derived.get('score'), 4)} | "
            f"{derived.get('active_count', 0)} | "
            f"{_fmt_number(derived.get('veto_pct'))}% |"
        )

    return "\n".join(lines)


def _render_markdown(summary: Dict[str, Any], top_results: List[Dict[str, Any]]) -> str:
    run_tag = summary.get("run_tag", "sweep")
    input_file = summary.get("input_file", "")
    record_count = summary.get("record_count", 0)
    total_configs = summary.get("total_configs", 0)
    selection_policy = summary.get("selection_policy", {})

    lines: List[str] = []
    lines.append(f"# Threshold Sweep Recommendation ({run_tag})")
    lines.append("")
    lines.append("## Run Metadata")
    lines.append(f"- Input file: {input_file}")
    lines.append(f"- Records: {record_count}")
    lines.append(f"- Total configs evaluated: {total_configs}")
    lines.append(f"- Max veto % policy: {selection_policy.get('max_veto_pct', '')}")
    lines.append("")

    lines.append("## Recommended Thresholds")
    lines.extend(_render_best_config(summary.get("best_config")))
    lines.append("")

    lines.append("## Top Ranked Configurations")
    lines.append(_render_top_table(top_results))
    lines.append("")

    lines.append("## Analyst Actions")
    lines.append("- Validate the best config on a larger historical dataset.")
    lines.append("- Compare selected config against current baseline in paper mode.")
    lines.append("- Promote to shadow mode only after risk and reliability checks pass.")
    lines.append("")

    lines.append("## Sign-off")
    lines.append("- Product:")
    lines.append("- Risk:")
    lines.append("- Engineering:")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate markdown recommendation report from threshold sweep output"
    )
    parser.add_argument(
        "--summary-json",
        required=True,
        help="Path to *_threshold_sweep_summary.json",
    )
    parser.add_argument(
        "--ranked-json",
        required=False,
        default=None,
        help="Optional path to *_threshold_sweep_ranked.json (uses summary top results if omitted)",
    )
    parser.add_argument(
        "--out-md",
        default=None,
        help="Output markdown path (default: summary path with .md extension)",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Top results to include")
    args = parser.parse_args()

    summary_path = Path(args.summary_json)
    summary = _load_json(summary_path)

    if args.ranked_json:
        ranked = _load_json(Path(args.ranked_json)).get("ranked_results", [])
    else:
        ranked = summary.get("top_results", [])

    top_k = max(args.top_k, 1)
    top_results = ranked[:top_k]

    markdown = _render_markdown(summary, top_results)

    out_md = Path(args.out_md) if args.out_md else summary_path.with_suffix(".md")
    out_md.write_text(markdown, encoding="utf-8")

    print(json.dumps({"markdown_report": str(out_md), "top_k": top_k}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
