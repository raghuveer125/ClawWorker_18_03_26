"""Closed-market audit helpers for strict adapter mode and known client call sites."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from .client import MarketDataClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_STRICT_EXPORTS = [
    Path("shared_project_engine/launcher/start.sh"),
    Path("ClawWork/start_paper_trading.sh"),
    Path("fyersN7/fyers-2026-03-05/scripts/start_all.sh"),
]

CLIENT_CALLSITE_CLASSIFICATION = {
    "runtime_managed": {
        "ClawWork/paper_trading_runner.py",
        "ClawWork/livebench/api/server.py",
        "ClawWork/livebench/tools/direct_tools.py",
        "ClawWork/livebench/trading/screener.py",
        "fyersN7/fyers-2026-03-05/scripts/pull_fyers_signal.py",
    },
    "manual_script": {
        "ClawWork/scripts/fyers_screener.sh",
    },
    "utility": {
        "ClawWork/livebench/backtesting/backtest.py",
        "ClawWork/institutional_agents/phase1_scaffold/fyers_to_phase1_csv.py",
    },
}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit strict adapter mode and known MarketDataClient call sites.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--json", action="store_true", help="Print raw JSON report.")
    return parser


def _classify_client_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    for label, known_paths in CLIENT_CALLSITE_CLASSIFICATION.items():
        if normalized in known_paths:
            return label
    return "unclassified"


def _scan_market_client_calls(project_root: Path) -> List[Dict[str, Any]]:
    callsites: List[Dict[str, Any]] = []
    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in {".py", ".sh"}:
            continue
        if any(part in {"node_modules", ".git", "__pycache__", "dist"} for part in path.parts):
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        relative_path = path.relative_to(project_root).as_posix()
        if relative_path.startswith("shared_project_engine/market/"):
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            if "MarketDataClient(" not in line:
                continue
            if line.lstrip().startswith("class MarketDataClient("):
                continue
            callsites.append(
                {
                    "path": relative_path,
                    "line": line_number,
                    "classification": _classify_client_path(relative_path),
                    "requests_local_fallback": "fallback_to_local=" in line,
                    "line_text": line.strip(),
                }
            )
    return callsites


def _check_required_strict_exports(project_root: Path) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for relative_path in REQUIRED_STRICT_EXPORTS:
        path = project_root / relative_path
        assignment_ok = False
        export_ok = False
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            assignment_ok = 'MARKET_ADAPTER_STRICT="${MARKET_ADAPTER_STRICT:-1}"' in text
            export_ok = "export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL MARKET_ADAPTER_STRICT" in text
        results.append(
            {
                "path": relative_path.as_posix(),
                "exists": path.exists(),
                "assignment_ok": assignment_ok,
                "export_ok": export_ok,
            }
        )
    return results


def _check_script_has_strict_export(project_root: Path, relative_path: str) -> bool:
    path = project_root / relative_path
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        'MARKET_ADAPTER_STRICT="${MARKET_ADAPTER_STRICT:-1}"' in text
        and "export MARKET_ADAPTER_HOST MARKET_ADAPTER_PORT MARKET_ADAPTER_URL MARKET_ADAPTER_STRICT" in text
    )


def _check_strict_runtime_behavior() -> Dict[str, Any]:
    client = MarketDataClient(service_url="", fallback_to_local=True, strict_mode=True)
    return {
        "strict_mode": client.strict_mode,
        "fallback_to_local": client.fallback_to_local,
        "adapter_initialized": client.adapter is not None,
        "ok": client.strict_mode and not client.fallback_to_local and client.adapter is None,
    }


def build_report(project_root: Path) -> Dict[str, Any]:
    strict_exports = _check_required_strict_exports(project_root)
    callsites = _scan_market_client_calls(project_root)
    strict_runtime = _check_strict_runtime_behavior()

    issues: List[str] = []
    warnings: List[str] = []

    for export_result in strict_exports:
        if not export_result["exists"]:
            issues.append(f"Missing strict launcher file: {export_result['path']}")
            continue
        if not export_result["assignment_ok"]:
            issues.append(f"Missing MARKET_ADAPTER_STRICT default in {export_result['path']}")
        if not export_result["export_ok"]:
            issues.append(f"Missing MARKET_ADAPTER_STRICT export in {export_result['path']}")

    if not strict_runtime["ok"]:
        issues.append("MarketDataClient strict_mode=True still allows local fallback")

    for callsite in callsites:
        if callsite["classification"] == "unclassified":
            issues.append(f"Unclassified MarketDataClient call site: {callsite['path']}:{callsite['line']}")
        elif (
            callsite["classification"] == "manual_script"
            and callsite["requests_local_fallback"]
            and not _check_script_has_strict_export(project_root, callsite["path"])
        ):
            warnings.append(
                f"Manual script relies on caller env for strict mode: {callsite['path']}:{callsite['line']}"
            )

    counts_by_classification: Dict[str, int] = {}
    for callsite in callsites:
        label = callsite["classification"]
        counts_by_classification[label] = counts_by_classification.get(label, 0) + 1

    return {
        "status": "ok" if not issues else "warn",
        "project_root": str(project_root),
        "strict_runtime": strict_runtime,
        "strict_exports": strict_exports,
        "client_callsites": callsites,
        "callsites_by_classification": counts_by_classification,
        "issues": issues,
        "warnings": warnings,
    }


def render_text_report(report: Dict[str, Any]) -> str:
    lines = [
        "Market Adapter Strict-Mode Audit",
        f"Status: {report['status']}",
        f"Project root: {report['project_root']}",
        "",
        "Strict runtime behavior",
        f"strict_mode: {report['strict_runtime']['strict_mode']}",
        f"fallback_to_local: {report['strict_runtime']['fallback_to_local']}",
        f"adapter_initialized: {report['strict_runtime']['adapter_initialized']}",
        "",
        "Required launcher exports",
    ]

    for result in report["strict_exports"]:
        lines.append(
            f"- {result['path']}: exists={result['exists']} assignment_ok={result['assignment_ok']} export_ok={result['export_ok']}"
        )

    lines.extend(["", "MarketDataClient call sites"])
    for label, count in sorted(report["callsites_by_classification"].items()):
        lines.append(f"- {label}: {count}")

    if report["issues"]:
        lines.extend(["", "Issues"])
        lines.extend(f"- {issue}" for issue in report["issues"])

    if report["warnings"]:
        lines.extend(["", "Warnings"])
        lines.extend(f"- {warning}" for warning in report["warnings"])

    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    project_root = Path(args.project_root).resolve()
    report = build_report(project_root)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text_report(report))

    return 0 if not report["issues"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
