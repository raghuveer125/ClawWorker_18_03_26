from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def evaluate_rollback(
    elapsed_seconds: int,
    sla_seconds: int,
    post_adapter_enabled: bool,
    post_shadow_mode: bool,
    post_dry_run: bool,
    post_allow_live_orders: bool,
    post_health_ok: bool,
) -> Dict[str, Any]:
    checks = {
        "rollback_within_sla": int(elapsed_seconds) <= int(sla_seconds),
        "adapter_disabled": not bool(post_adapter_enabled),
        "shadow_mode_safe": bool(post_shadow_mode),
        "dry_run_enabled": bool(post_dry_run),
        "live_orders_blocked": not bool(post_allow_live_orders),
        "post_rollback_health_ok": bool(post_health_ok),
    }

    reasons: List[str] = [name for name, passed in checks.items() if not passed]

    return {
        "generated_at": _now_utc(),
        "passed": all(checks.values()),
        "checks": checks,
        "reasons": reasons,
        "metrics": {
            "elapsed_seconds": int(elapsed_seconds),
            "sla_seconds": int(sla_seconds),
            "post_adapter_enabled": bool(post_adapter_enabled),
            "post_shadow_mode": bool(post_shadow_mode),
            "post_dry_run": bool(post_dry_run),
            "post_allow_live_orders": bool(post_allow_live_orders),
            "post_health_ok": bool(post_health_ok),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate rollback drill result artifact for integration rollout")
    parser.add_argument("--stage", default="stage1_5pct")
    parser.add_argument("--elapsed-seconds", type=int, required=True)
    parser.add_argument("--sla-seconds", type=int, default=300)

    parser.add_argument("--post-adapter-enabled", type=_to_bool, required=True)
    parser.add_argument("--post-shadow-mode", type=_to_bool, default=True)
    parser.add_argument("--post-dry-run", type=_to_bool, default=True)
    parser.add_argument("--post-allow-live-orders", type=_to_bool, default=False)
    parser.add_argument("--post-health-ok", type=_to_bool, required=True)

    parser.add_argument("--out-json", required=True)
    args = parser.parse_args()

    payload = evaluate_rollback(
        elapsed_seconds=int(args.elapsed_seconds),
        sla_seconds=int(args.sla_seconds),
        post_adapter_enabled=bool(args.post_adapter_enabled),
        post_shadow_mode=bool(args.post_shadow_mode),
        post_dry_run=bool(args.post_dry_run),
        post_allow_live_orders=bool(args.post_allow_live_orders),
        post_health_ok=bool(args.post_health_ok),
    )
    payload["stage"] = args.stage

    out_path = Path(args.out_json)
    _write_json(out_path, payload)

    print(json.dumps({"rollback_report": str(out_path), "passed": payload.get("passed", False), "reasons": payload.get("reasons", [])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
