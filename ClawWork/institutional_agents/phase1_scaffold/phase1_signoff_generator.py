from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _status(flag: bool) -> str:
    return "PASS" if flag else "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Phase 1 sign-off note from release and report artifacts")
    parser.add_argument("--tag", required=True, help="Run tag used in phase1 artifacts")
    parser.add_argument("--outdir", default="reports", help="Artifacts directory")
    parser.add_argument("--out-md", default=None, help="Output markdown path")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    tag = args.tag

    release_path = outdir / f"{tag}_release_check.json"
    sweep_summary_path = outdir / f"{tag}_sweep_threshold_sweep_summary.json"
    pipeline_summary_path = outdir / f"{tag}_pipeline_summary.json"

    # Support direct use without persisted release-check json by reading expected files.
    release_payload = {
        "passed": False,
        "checked_files": [],
        "missing_files": [],
        "validation_errors": ["release check payload not found; generate with phase1_release_check.py and save json"],
    }

    if release_path.exists():
        release_payload = _load_json(release_path)
    else:
        # Derive a lightweight release status from existing artifacts
        required = [
            outdir / f"{tag}_sweep_threshold_sweep_summary.json",
            outdir / f"{tag}_sweep_threshold_sweep_ranked.json",
            outdir / f"{tag}_sweep_threshold_sweep_summary.md",
            outdir / f"{tag}_pipeline_report.json",
            outdir / f"{tag}_pipeline_summary.json",
            outdir / f"{tag}_pipeline_report.md",
        ]
        missing = [str(path) for path in required if not path.exists()]
        release_payload = {
            "passed": len(missing) == 0,
            "checked_files": [str(path) for path in required if path.exists()],
            "missing_files": missing,
            "validation_errors": [],
        }

    sweep_payload: Dict[str, Any] = _load_json(sweep_summary_path) if sweep_summary_path.exists() else {}
    pipeline_summary_payload: Dict[str, Any] = _load_json(pipeline_summary_path) if pipeline_summary_path.exists() else {}

    best = sweep_payload.get("best_config", {}) if isinstance(sweep_payload, dict) else {}
    best_cfg = best.get("config", {}) if isinstance(best, dict) else {}
    best_derived = best.get("derived", {}) if isinstance(best, dict) else {}

    action_counts = pipeline_summary_payload.get("action_counts", {})
    confidence_counts = pipeline_summary_payload.get("confidence_counts", {})

    passed = bool(release_payload.get("passed", False))
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")

    lines = [
        f"# Phase 1 Sign-off Note ({tag})",
        "",
        f"Generated at: {now_utc}",
        "",
        "## Release Gate",
        f"- Status: {_status(passed)}",
        f"- Checked artifacts: {len(release_payload.get('checked_files', []))}",
        f"- Missing artifacts: {len(release_payload.get('missing_files', []))}",
        f"- Validation errors: {len(release_payload.get('validation_errors', []))}",
        "",
        "## Best Threshold Recommendation",
        f"- Bullish threshold: {_fmt(best_cfg.get('bullish_threshold'))}",
        f"- Bearish threshold: {_fmt(best_cfg.get('bearish_threshold'))}",
        f"- Strong-move threshold: {_fmt(best_cfg.get('strong_move_threshold'))}",
        f"- Heuristic score: {_fmt(best_derived.get('score'), 4)}",
        f"- Active decisions: {best_derived.get('active_count', 0)}",
        f"- Risk veto %: {_fmt(best_derived.get('veto_pct'))}%",
        "",
        "## Pipeline Summary",
        f"- Total decisions: {pipeline_summary_payload.get('total_decisions', 0)}",
        f"- BUY_CALL: {action_counts.get('BUY_CALL', 0)}",
        f"- BUY_PUT: {action_counts.get('BUY_PUT', 0)}",
        f"- NO_TRADE: {action_counts.get('NO_TRADE', 0)}",
        f"- Confidence HIGH/MEDIUM/LOW: {confidence_counts.get('HIGH', 0)}/{confidence_counts.get('MEDIUM', 0)}/{confidence_counts.get('LOW', 0)}",
        f"- Risk veto count: {pipeline_summary_payload.get('risk_veto_count', 0)}",
        f"- Risk veto %: {_fmt(pipeline_summary_payload.get('risk_veto_pct'))}%",
        "",
        "## Decision",
        f"- Phase 1 release candidate: {'APPROVED' if passed else 'HOLD'}",
        "- Next action: proceed to remaining Phase 1 checklist items and final sign-off review.",
        "",
        "## Signatures",
        "- Product Owner:",
        "- Risk Owner:",
        "- Engineering Owner:",
    ]

    out_md = Path(args.out_md) if args.out_md else outdir / f"{tag}_phase1_signoff.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"signoff_markdown": str(out_md), "passed": passed}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
