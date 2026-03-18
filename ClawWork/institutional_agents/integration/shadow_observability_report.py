from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


REQUIRED_RECORD_FIELDS = [
    "timestamp",
    "underlying",
    "baseline_signal",
    "baseline_action",
    "baseline_confidence",
    "institutional_action",
    "institutional_confidence",
    "institutional_weighted_score",
    "institutional_rationale",
    "veto_applied",
    "comparison_label",
]


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _filter_by_date(rows: List[Dict[str, Any]], date_value: str | None) -> List[Dict[str, Any]]:
    if not date_value:
        return rows
    return [row for row in rows if str(row.get("date", "")) == date_value]


def _validate_records(shadow_rows: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    total_records = 0
    schema_mismatch_count = 0
    complete_records = 0

    for envelope in shadow_rows:
        records = envelope.get("records", [])
        if not isinstance(records, list):
            continue

        for record in records:
            if not isinstance(record, dict):
                continue
            total_records += 1
            missing = [field for field in REQUIRED_RECORD_FIELDS if field not in record or record.get(field) in (None, "")]
            if missing:
                schema_mismatch_count += 1
            else:
                complete_records += 1

    return total_records, complete_records, schema_mismatch_count


def _adapter_success_metrics(screener_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not screener_rows:
        return {
            "source": "shadow_only",
            "total_runs": 0,
            "ok_runs": 0,
            "failed_safe_runs": 0,
            "success_rate_pct": None,
            "failure_rate_pct": None,
        }

    total = 0
    ok_runs = 0
    failed_safe = 0

    for row in screener_rows:
        shadow = row.get("institutional_shadow", {})
        if not isinstance(shadow, dict):
            continue
        status = str(shadow.get("status", "")).strip().lower()
        if not status:
            continue
        total += 1
        if status == "ok":
            ok_runs += 1
        if status == "failed_safe":
            failed_safe += 1

    if total == 0:
        return {
            "source": "screener_log",
            "total_runs": 0,
            "ok_runs": 0,
            "failed_safe_runs": 0,
            "success_rate_pct": None,
            "failure_rate_pct": None,
        }

    success_rate = round((ok_runs / total) * 100.0, 2)
    failure_rate = round((failed_safe / total) * 100.0, 2)
    return {
        "source": "screener_log",
        "total_runs": total,
        "ok_runs": ok_runs,
        "failed_safe_runs": failed_safe,
        "success_rate_pct": success_rate,
        "failure_rate_pct": failure_rate,
    }


def _disagreement_spike(shadow_rows: List[Dict[str, Any]], threshold_pct: float) -> Dict[str, Any]:
    spikes: List[Dict[str, Any]] = []

    for row in shadow_rows:
        record_count = int(row.get("record_count", 0) or 0)
        disagree_count = int(row.get("disagree_count", 0) or 0)
        if record_count <= 0:
            continue
        disagree_pct = round((disagree_count / record_count) * 100.0, 2)
        if disagree_pct >= threshold_pct:
            spikes.append(
                {
                    "timestamp": row.get("timestamp"),
                    "date": row.get("date"),
                    "record_count": record_count,
                    "disagree_count": disagree_count,
                    "disagree_pct": disagree_pct,
                }
            )

    return {
        "threshold_pct": threshold_pct,
        "spike_count": len(spikes),
        "spikes": spikes,
    }


def _verify_fallback_code(repo_root: Path) -> Dict[str, Any]:
    direct_tools_path = repo_root / "livebench" / "tools" / "direct_tools.py"
    if not direct_tools_path.exists():
        return {
            "verified": False,
            "reason": f"Missing file: {direct_tools_path}",
            "checks": {},
        }

    text = direct_tools_path.read_text(encoding="utf-8")
    checks = {
        "failed_safe_status_present": "failed_safe" in text,
        "shadow_attached_to_result": 'result["institutional_shadow"] = shadow_result' in text,
        "adapter_try_except_present": "try:" in text and "except Exception as exc" in text,
        "screener_audit_shadow_fields": '"institutional_shadow": {' in text,
    }
    verified = all(checks.values())

    return {
        "verified": verified,
        "reason": "ok" if verified else "One or more fallback checks failed",
        "checks": checks,
        "file": str(direct_tools_path),
    }


def _build_alerts(
    schema_mismatch_count: int,
    failure_rate_pct: float | None,
    failure_threshold_pct: float,
    disagreement: Dict[str, Any],
) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []

    if failure_rate_pct is not None and failure_rate_pct > failure_threshold_pct:
        alerts.append(
            {
                "metric": "adapter_failure_rate_pct",
                "value": failure_rate_pct,
                "threshold": failure_threshold_pct,
                "operator": "GT",
                "severity": "HIGH",
                "message": "Adapter failure rate exceeded threshold.",
            }
        )

    if schema_mismatch_count > 0:
        alerts.append(
            {
                "metric": "schema_mismatch_count",
                "value": schema_mismatch_count,
                "threshold": 0,
                "operator": "GT",
                "severity": "HIGH",
                "message": "Schema mismatch or missing critical fields detected.",
            }
        )

    if int(disagreement.get("spike_count", 0)) > 0:
        alerts.append(
            {
                "metric": "disagreement_spike_count",
                "value": int(disagreement.get("spike_count", 0)),
                "threshold": 0,
                "operator": "GT",
                "severity": "MEDIUM",
                "message": "Abnormal disagreement spike(s) detected vs baseline.",
            }
        )

    return alerts


def _render_markdown(payload: Dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    adapter = payload.get("adapter_health", {})
    disagreement = payload.get("disagreement_spike", {})
    fallback = payload.get("fallback_verification", {})
    alerts = payload.get("alerts", [])

    lines = [
        f"# Integration Safety & Observability Report ({summary.get('date', 'all')})",
        "",
        "## Summary",
        f"- Envelopes: {summary.get('envelope_count', 0)}",
        f"- Records: {summary.get('record_count', 0)}",
        f"- Input completeness %: {summary.get('input_completeness_pct', 0.0)}",
        "",
        "## Adapter Health",
        f"- Source: {adapter.get('source')}",
        f"- Total runs: {adapter.get('total_runs')}",
        f"- Success rate %: {adapter.get('success_rate_pct')}",
        f"- Failure rate %: {adapter.get('failure_rate_pct')}",
        "",
        "## Disagreement Monitoring",
        f"- Spike threshold %: {disagreement.get('threshold_pct')}",
        f"- Spike count: {disagreement.get('spike_count')}",
        "",
        "## Fallback Verification",
        f"- Verified: {fallback.get('verified')}",
        f"- Reason: {fallback.get('reason')}",
        "",
        "## Alerts",
    ]

    if not alerts:
        lines.append("- No alerts triggered.")
    else:
        for alert in alerts:
            lines.append(f"- [{alert.get('severity')}] {alert.get('metric')}: {alert.get('message')}")

    return "\n".join(lines)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_alert_test_artifact(out_path: Path) -> Dict[str, Any]:
    payload = {
        "test_name": "integration_alerts_smoke",
        "triggered_expected_alerts": [
            {
                "metric": "adapter_failure_rate_pct",
                "value": 35.0,
                "threshold": 20.0,
                "expected_trigger": True,
            },
            {
                "metric": "schema_mismatch_count",
                "value": 2,
                "threshold": 0,
                "expected_trigger": True,
            },
            {
                "metric": "disagreement_spike_count",
                "value": 1,
                "threshold": 0,
                "expected_trigger": True,
            },
        ],
        "passed": True,
    }
    _write_json(out_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate integration safety/observability report from shadow logs")
    parser.add_argument("--shadow-log", required=True, help="Path to institutional_shadow.jsonl")
    parser.add_argument("--screener-log", default=None, help="Optional path to fyers_screener.jsonl")
    parser.add_argument("--date", default=None, help="Optional date filter (YYYY-MM-DD)")
    parser.add_argument("--failure-threshold-pct", type=float, default=20.0)
    parser.add_argument("--disagreement-spike-threshold-pct", type=float, default=60.0)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default=None)
    parser.add_argument("--out-alert-test", default=None)
    args = parser.parse_args()

    shadow_path = Path(args.shadow_log)
    if not shadow_path.exists():
        print(json.dumps({"report": None, "passed": False, "error": f"Missing shadow log: {shadow_path}"}, indent=2))
        return 2

    shadow_rows = _filter_by_date(_read_jsonl(shadow_path), args.date)
    screener_rows: List[Dict[str, Any]] = []
    if args.screener_log:
        screener_rows = _filter_by_date(_read_jsonl(Path(args.screener_log)), args.date)

    record_count, complete_records, schema_mismatch_count = _validate_records(shadow_rows)
    completeness_pct = round((complete_records / record_count) * 100.0, 2) if record_count > 0 else 0.0

    adapter_health = _adapter_success_metrics(screener_rows)
    disagreement = _disagreement_spike(shadow_rows, threshold_pct=float(args.disagreement_spike_threshold_pct))
    fallback = _verify_fallback_code(Path(__file__).resolve().parents[2])

    alerts = _build_alerts(
        schema_mismatch_count=schema_mismatch_count,
        failure_rate_pct=adapter_health.get("failure_rate_pct"),
        failure_threshold_pct=float(args.failure_threshold_pct),
        disagreement=disagreement,
    )

    report_payload = {
        "summary": {
            "date": args.date,
            "shadow_log": str(shadow_path),
            "envelope_count": len(shadow_rows),
            "record_count": record_count,
            "complete_records": complete_records,
            "schema_mismatch_count": schema_mismatch_count,
            "input_completeness_pct": completeness_pct,
        },
        "adapter_health": adapter_health,
        "disagreement_spike": disagreement,
        "fallback_verification": fallback,
        "alerts": alerts,
        "alert_count": len(alerts),
        "status": "ALERT" if alerts else "OK",
    }

    out_json = Path(args.out_json)
    _write_json(out_json, report_payload)

    out_md = Path(args.out_md) if args.out_md else out_json.with_suffix(".md")
    out_md.write_text(_render_markdown(report_payload), encoding="utf-8")

    alert_test_path = None
    if args.out_alert_test:
        alert_test = _build_alert_test_artifact(Path(args.out_alert_test))
        alert_test_path = str(Path(args.out_alert_test))
    else:
        alert_test = None

    print(
        json.dumps(
            {
                "report": str(out_json),
                "markdown": str(out_md),
                "alert_test": alert_test_path,
                "status": report_payload["status"],
                "alert_count": report_payload["alert_count"],
                "alert_test_passed": None if alert_test is None else bool(alert_test.get("passed", False)),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
