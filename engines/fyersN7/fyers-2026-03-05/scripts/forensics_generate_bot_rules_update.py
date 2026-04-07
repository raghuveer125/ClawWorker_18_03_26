#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.utils import to_float_opt as to_float, to_int_opt as to_int


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_csv_list(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate bot_rules_update from high-confidence pattern templates."
    )
    p.add_argument("--base-dir", default="postmortem", help="Base folder containing date subfolders")
    p.add_argument("--date", default="", help="Date folder in YYYY-MM-DD (default: latest available)")
    p.add_argument("--symbols", default="SENSEX,NIFTY50", help="Comma-separated symbol allowlist")
    p.add_argument(
        "--input-dir",
        default="",
        help="Input directory containing pattern_templates/run_summary (default: <base>/<date>)",
    )
    p.add_argument(
        "--output-json",
        default="",
        help="Output JSON path (default: <base>/<date>/bot_rules_update_<date>.json)",
    )
    p.add_argument(
        "--approval-mode",
        default="auto",
        choices=["manual", "auto"],
        help="manual: always pending review, auto: allow auto-approval when all gates pass",
    )
    p.add_argument(
        "--max-proposals",
        type=int,
        default=20,
        help="Maximum number of rule proposals to emit",
    )
    p.add_argument("--min-confidence", type=float, default=60.0, help="Minimum rule confidence score")
    p.add_argument("--min-sample-count", type=int, default=8, help="Minimum sample_count to consider")
    p.add_argument("--min-hit-rate", type=float, default=85.0, help="Minimum hit_rate to consider")
    p.add_argument("--min-expectancy", type=float, default=0.10, help="Minimum expectancy to consider")
    p.add_argument("--min-decay-score", type=float, default=0.50, help="Minimum decay_score to consider")
    p.add_argument(
        "--approval-start-date",
        default="",
        help="YYYY-MM-DD day-zero for manual lock window (if empty, earliest available date is used)",
    )
    p.add_argument(
        "--manual-lock-days",
        type=int,
        default=14,
        help="Days to keep manual-only mode from approval-start-date",
    )
    p.add_argument(
        "--quality-lookback-days",
        type=int,
        default=5,
        help="Lookback days for quality gate",
    )
    p.add_argument(
        "--quality-min-score",
        type=float,
        default=95.0,
        help="Minimum quality score required per day in lookback window",
    )
    p.add_argument(
        "--gate2-min-pattern-samples",
        type=int,
        default=30,
        help="Gate2 threshold: minimum sample count per proposed pattern",
    )
    p.add_argument(
        "--walkforward-pass",
        type=int,
        default=-1,
        help="Gate3 input (1=pass, 0=fail, -1=auto proxy from proposals)",
    )
    p.add_argument(
        "--walkforward-proxy-min-avg-expectancy",
        type=float,
        default=0.12,
        help="Gate3 proxy threshold: weighted avg expectancy across proposals",
    )
    p.add_argument(
        "--walkforward-proxy-min-avg-hit-rate",
        type=float,
        default=90.0,
        help="Gate3 proxy threshold: weighted avg hit_rate across proposals",
    )
    p.add_argument(
        "--projected-drawdown-delta",
        default="auto",
        help="Gate4 input: drawdown delta pct or 'auto' to derive from proposals",
    )
    p.add_argument(
        "--max-drawdown-worse-pct",
        type=float,
        default=5.0,
        help="Gate4 threshold: must not be worse than baseline by more than this pct",
    )
    p.add_argument(
        "--deployment-mode",
        default="canary",
        choices=["canary", "paper", "full"],
        help="Deployment mode requested for approved rules",
    )
    p.add_argument(
        "--canary-required",
        type=int,
        choices=[0, 1],
        default=1,
        help="Gate6 requires canary/paper deployment when set to 1",
    )
    p.add_argument(
        "--enable-auto-rollback",
        type=int,
        choices=[0, 1],
        default=1,
        help="Enable rollback checks from canary/live metrics",
    )
    p.add_argument(
        "--canary-metrics-json",
        default="",
        help="Optional JSON file with canary/live observed metrics",
    )
    p.add_argument(
        "--canary-current-drawdown-pct",
        default="",
        help="Observed canary drawdown pct (override)",
    )
    p.add_argument(
        "--canary-max-drawdown-pct",
        type=float,
        default=3.0,
        help="Rollback threshold: max allowed canary drawdown pct",
    )
    p.add_argument(
        "--canary-current-win-rate-delta-pct",
        default="",
        help="Observed canary win-rate delta pct vs baseline (override)",
    )
    p.add_argument(
        "--canary-min-win-rate-delta-pct",
        type=float,
        default=-2.0,
        help="Rollback threshold: minimum allowed win-rate delta pct",
    )
    p.add_argument(
        "--canary-current-error-rate-pct",
        default="",
        help="Observed canary error rate pct (override)",
    )
    p.add_argument(
        "--canary-max-error-rate-pct",
        type=float,
        default=2.0,
        help="Rollback threshold: maximum allowed canary error rate pct",
    )
    p.add_argument(
        "--canary-consecutive-losses",
        default="",
        help="Observed consecutive losses in canary mode (override)",
    )
    p.add_argument(
        "--canary-max-consecutive-losses",
        type=int,
        default=4,
        help="Rollback threshold: maximum allowed consecutive losses",
    )
    p.add_argument(
        "--canary-observation-trades",
        default="",
        help="Observed trade count in canary mode (override)",
    )
    p.add_argument(
        "--canary-min-observation-trades",
        type=int,
        default=8,
        help="Minimum observations before win-rate rollback check is enforced",
    )
    p.add_argument(
        "--rollback-halt-days",
        type=int,
        default=2,
        help="Suggested halt window in days when rollback is triggered",
    )
    p.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero on missing critical input files or failed status.",
    )
    return p.parse_args()


def detect_latest_date_folder(base_dir: Path) -> str:
    candidates: List[str] = []
    if not base_dir.exists():
        return ""
    for child in base_dir.iterdir():
        if child.is_dir() and DATE_RE.match(child.name):
            candidates.append(child.name)
    if not candidates:
        return ""
    candidates.sort()
    return candidates[-1]


def resolve_date(base_dir: Path, requested_date: str) -> Tuple[str, str]:
    if requested_date:
        if not DATE_RE.match(requested_date):
            return "", f"Invalid --date format: {requested_date} (expected YYYY-MM-DD)"
        return requested_date, ""
    latest = detect_latest_date_folder(base_dir)
    if not latest:
        return "", f"No date folders found under: {base_dir}"
    return latest, ""


def list_date_folders(base_dir: Path) -> List[str]:
    out: List[str] = []
    if not base_dir.exists():
        return out
    for child in base_dir.iterdir():
        if child.is_dir() and DATE_RE.match(child.name):
            out.append(child.name)
    out.sort()
    return out


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]], str]:
    if not path.exists():
        return [], [], "missing"
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            headers = [h.strip() for h in (reader.fieldnames or []) if h]
            if not headers:
                return [], [], "missing_header"
            rows: List[Dict[str, str]] = []
            for raw in reader:
                row: Dict[str, str] = {}
                for h in headers:
                    row[h] = (raw.get(h, "") or "").strip()
                rows.append(row)
        return headers, rows, ""
    except Exception as exc:
        return [], [], f"parse_error:{type(exc).__name__}"


def read_json(path: Path) -> Tuple[Dict[str, object], str]:
    if not path.exists():
        return {}, "missing"
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            return payload, ""
        return {}, "invalid_json_root"
    except Exception as exc:
        return {}, f"parse_error:{type(exc).__name__}"


def rule_id(date_str: str, symbol: str, pattern_id_raw: str) -> str:
    base = f"{date_str}|{symbol}|{pattern_id_raw}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"RULE_{digest}"


def parse_trigger_combo(combo: str) -> Tuple[str, str, str]:
    parts = [x.strip() for x in (combo or "").split("|")]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def compute_rule_confidence(
    sample_count: int,
    hit_rate: float,
    expectancy: float,
    decay_score: float,
    avg_score: float,
) -> float:
    sample_score = min(35.0, max(0.0, sample_count / 30.0 * 35.0))
    hit_score = min(25.0, max(0.0, hit_rate / 100.0 * 25.0))
    expectancy_norm = max(0.0, min(1.0, (expectancy + 1.0) / 2.0))
    expectancy_score = expectancy_norm * 20.0
    decay = max(0.0, min(1.0, decay_score))
    decay_component = decay * 10.0
    avg_score_component = min(10.0, max(0.0, (avg_score - 50.0) / 50.0 * 10.0))
    return max(0.0, min(100.0, sample_score + hit_score + expectancy_score + decay_component + avg_score_component))


def compose_rule_logic(
    regime: str,
    trigger_type: str,
    recommendation: str,
    vol_state: str,
) -> Dict[str, object]:
    rec = (recommendation or "").upper()
    trg = (trigger_type or "").upper()
    reg = (regime or "").upper()
    vol = (vol_state or "").upper()

    if rec == "BULLISH":
        side = "LONG"
        entry_condition = f"{trg} in {reg} ({vol}) -> prefer long entries on confirmation candle close."
        risk_adjustment = {"position_size_mult": 1.10, "sl_buffer_mult": 0.95, "max_reentry": 1}
        exit_adjustment = {"take_profit_bias": "hold_winners", "trail_after_t1": True}
    elif rec == "BEARISH":
        side = "SHORT"
        entry_condition = f"{trg} in {reg} ({vol}) -> prefer short entries on confirmation candle close."
        risk_adjustment = {"position_size_mult": 1.10, "sl_buffer_mult": 0.95, "max_reentry": 1}
        exit_adjustment = {"take_profit_bias": "hold_winners", "trail_after_t1": True}
    elif rec == "REDUCE_LONG_RISK":
        side = "LONG_RISK_OFF"
        entry_condition = f"{trg} in {reg} ({vol}) -> reduce long risk, avoid fresh aggressive longs."
        risk_adjustment = {"position_size_mult": 0.75, "sl_buffer_mult": 0.90, "max_reentry": 0}
        exit_adjustment = {"take_profit_bias": "faster_profit_lock", "trail_after_t1": False}
    elif rec == "REDUCE_SHORT_RISK":
        side = "SHORT_RISK_OFF"
        entry_condition = f"{trg} in {reg} ({vol}) -> reduce short risk, avoid fresh aggressive shorts."
        risk_adjustment = {"position_size_mult": 0.75, "sl_buffer_mult": 0.90, "max_reentry": 0}
        exit_adjustment = {"take_profit_bias": "faster_profit_lock", "trail_after_t1": False}
    elif rec == "TAKE_PROFIT_OR_HEDGE":
        side = "RISK_MANAGEMENT"
        entry_condition = f"{trg} in {reg} ({vol}) -> prioritize hedge/profit booking over new risk."
        risk_adjustment = {"position_size_mult": 0.60, "sl_buffer_mult": 0.85, "max_reentry": 0}
        exit_adjustment = {"take_profit_bias": "take_profit_early", "trail_after_t1": False}
    else:
        side = "WATCH"
        entry_condition = f"{trg} in {reg} ({vol}) -> watchlist trigger; require extra confirmation."
        risk_adjustment = {"position_size_mult": 0.70, "sl_buffer_mult": 1.00, "max_reentry": 0}
        exit_adjustment = {"take_profit_bias": "neutral", "trail_after_t1": False}

    return {
        "side_bias": side,
        "entry_condition": entry_condition,
        "risk_adjustment": risk_adjustment,
        "exit_adjustment": exit_adjustment,
    }


def compute_quality_score(quality_payload: Dict[str, object], symbols: List[str]) -> Optional[float]:
    symbol_results = quality_payload.get("symbol_results", {})
    if not isinstance(symbol_results, dict):
        return None

    pass_checks = 0
    total_checks = 0
    for symbol in symbols:
        srow = symbol_results.get(symbol, {})
        if not isinstance(srow, dict):
            continue
        checks = srow.get("checks", [])
        if not isinstance(checks, list):
            continue
        for c in checks:
            if not isinstance(c, dict):
                continue
            total_checks += 1
            if str(c.get("status", "")).upper() == "PASS":
                pass_checks += 1

    if total_checks <= 0:
        return None
    return (pass_checks / total_checks) * 100.0


def evaluate_quality_gate(
    base_dir: Path,
    date_str: str,
    symbols: List[str],
    lookback_days: int,
    min_score: float,
) -> Dict[str, object]:
    history = [d for d in list_date_folders(base_dir) if d <= date_str]
    history = history[-max(1, lookback_days):]
    day_scores: List[Dict[str, object]] = []
    missing_days: List[str] = []

    for d in history:
        p = base_dir / d / f"quality_summary_{d}.json"
        payload, err = read_json(p)
        if err:
            missing_days.append(d)
            continue
        score = compute_quality_score(payload, symbols)
        if score is None:
            missing_days.append(d)
            continue
        day_scores.append({"date": d, "quality_score": round(score, 2)})

    pass_flag = (
        len(day_scores) >= max(1, lookback_days)
        and not missing_days
        and all(float(x["quality_score"]) >= min_score for x in day_scores)
    )
    reason = "ok" if pass_flag else (
        "insufficient_history_or_below_threshold"
    )
    return {
        "pass": pass_flag,
        "threshold": min_score,
        "lookback_days": lookback_days,
        "scored_days": day_scores,
        "missing_days": missing_days,
        "reason": reason,
    }


def infer_approval_start_date(base_dir: Path, date_str: str) -> Tuple[str, str]:
    history = [d for d in list_date_folders(base_dir) if d <= date_str]
    if not history:
        return "", "no_date_folders"
    return history[0], "inferred_from_earliest_available_date"


def evaluate_walkforward_gate(args: argparse.Namespace, proposals: List[Dict[str, object]]) -> Dict[str, object]:
    mode = "explicit"
    raw = int(args.walkforward_pass)
    if raw in (0, 1):
        return {
            "pass": raw == 1,
            "mode": mode,
            "raw_input": raw,
            "reason": "ok" if raw == 1 else "walkforward_not_beating_baseline",
        }

    # Auto proxy mode from proposal-quality aggregates.
    mode = "auto_proxy"
    if not proposals:
        return {
            "pass": False,
            "mode": mode,
            "raw_input": raw,
            "weighted_avg_expectancy": None,
            "weighted_avg_hit_rate": None,
            "min_avg_expectancy": float(args.walkforward_proxy_min_avg_expectancy),
            "min_avg_hit_rate": float(args.walkforward_proxy_min_avg_hit_rate),
            "reason": "no_proposals_for_proxy",
        }

    total_weight = sum(max(1, int(p.get("sample_count", 0))) for p in proposals)
    weighted_avg_expectancy = (
        sum(float(p.get("expectancy", 0.0)) * max(1, int(p.get("sample_count", 0))) for p in proposals)
        / max(1, total_weight)
    )
    weighted_avg_hit_rate = (
        sum(float(p.get("hit_rate", 0.0)) * max(1, int(p.get("sample_count", 0))) for p in proposals)
        / max(1, total_weight)
    )
    min_avg_expectancy = float(args.walkforward_proxy_min_avg_expectancy)
    min_avg_hit_rate = float(args.walkforward_proxy_min_avg_hit_rate)
    pass_flag = (
        weighted_avg_expectancy >= min_avg_expectancy
        and weighted_avg_hit_rate >= min_avg_hit_rate
    )
    return {
        "pass": pass_flag,
        "mode": mode,
        "raw_input": raw,
        "weighted_avg_expectancy": round(weighted_avg_expectancy, 4),
        "weighted_avg_hit_rate": round(weighted_avg_hit_rate, 2),
        "min_avg_expectancy": min_avg_expectancy,
        "min_avg_hit_rate": min_avg_hit_rate,
        "reason": "ok" if pass_flag else "auto_proxy_threshold_not_met",
    }


def evaluate_drawdown_gate(
    args: argparse.Namespace,
    proposals: List[Dict[str, object]],
) -> Dict[str, object]:
    threshold = float(args.max_drawdown_worse_pct)
    raw = (args.projected_drawdown_delta or "").strip()
    raw_lower = raw.lower()

    if raw_lower in ("", "auto"):
        if not proposals:
            return {
                "pass": False,
                "mode": "auto_proxy",
                "raw_input": raw or "auto",
                "projected_drawdown_delta_pct": None,
                "threshold_pct": threshold,
                "reason": "no_proposals_for_proxy",
            }
        projected = max(
            float((p.get("expected_impact", {}) or {}).get("drawdown_delta_pct", 0.0))
            for p in proposals
        )
        pass_flag = projected <= threshold
        return {
            "pass": pass_flag,
            "mode": "auto_proxy",
            "raw_input": raw or "auto",
            "projected_drawdown_delta_pct": round(projected, 4),
            "threshold_pct": threshold,
            "reason": "ok" if pass_flag else "projected_drawdown_too_high",
        }

    parsed = to_float(raw)
    if parsed is None:
        return {
            "pass": False,
            "mode": "explicit",
            "raw_input": raw,
            "projected_drawdown_delta_pct": None,
            "threshold_pct": threshold,
            "reason": "invalid_projected_drawdown_input",
        }

    pass_flag = parsed <= threshold
    return {
        "pass": pass_flag,
        "mode": "explicit",
        "raw_input": raw,
        "projected_drawdown_delta_pct": round(parsed, 4),
        "threshold_pct": threshold,
        "reason": "ok" if pass_flag else "projected_drawdown_too_high",
    }


def _get_by_path(payload: Dict[str, object], path: str) -> Optional[object]:
    cur: object = payload
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur.get(part)
    return cur


def _pick_metric(
    *,
    override_raw: str,
    payload: Dict[str, object],
    paths: List[str],
    as_int: bool = False,
) -> Tuple[Optional[float], str]:
    raw = (override_raw or "").strip()
    if raw:
        val = to_int(raw) if as_int else to_float(raw)
        if val is None:
            return None, "override_invalid"
        return float(val), "override"

    for path in paths:
        node = _get_by_path(payload, path)
        if node is None:
            continue
        val = to_int(str(node)) if as_int else to_float(str(node))
        if val is None:
            continue
        return float(val), f"metrics_json:{path}"

    return None, "unavailable"


def evaluate_canary_rollback(
    args: argparse.Namespace,
    *,
    date_dir: Path,
    date_str: str,
    proposals: List[Dict[str, object]],
) -> Dict[str, object]:
    enabled = int(args.enable_auto_rollback) == 1
    metrics_path = Path(args.canary_metrics_json) if args.canary_metrics_json.strip() else (date_dir / f"canary_metrics_{date_str}.json")
    metrics_payload: Dict[str, object] = {}
    metrics_err = ""
    if metrics_path.exists():
        metrics_payload, metrics_err = read_json(metrics_path)
    else:
        metrics_err = "missing"

    drawdown, drawdown_src = _pick_metric(
        override_raw=args.canary_current_drawdown_pct,
        payload=metrics_payload,
        paths=[
            "canary_metrics.current_drawdown_pct",
            "canary_metrics.drawdown_pct",
            "current_drawdown_pct",
            "drawdown_pct",
        ],
    )
    winrate_delta, winrate_src = _pick_metric(
        override_raw=args.canary_current_win_rate_delta_pct,
        payload=metrics_payload,
        paths=[
            "canary_metrics.win_rate_delta_pct",
            "win_rate_delta_pct",
        ],
    )
    error_rate, error_src = _pick_metric(
        override_raw=args.canary_current_error_rate_pct,
        payload=metrics_payload,
        paths=[
            "canary_metrics.error_rate_pct",
            "error_rate_pct",
        ],
    )
    consecutive_losses, losses_src = _pick_metric(
        override_raw=args.canary_consecutive_losses,
        payload=metrics_payload,
        paths=[
            "canary_metrics.consecutive_losses",
            "consecutive_losses",
        ],
        as_int=True,
    )
    observation_trades, obs_src = _pick_metric(
        override_raw=args.canary_observation_trades,
        payload=metrics_payload,
        paths=[
            "canary_metrics.observation_trades",
            "canary_metrics.trades",
            "observation_trades",
            "trades",
        ],
        as_int=True,
    )

    thresholds = {
        "max_drawdown_pct": float(args.canary_max_drawdown_pct),
        "min_win_rate_delta_pct": float(args.canary_min_win_rate_delta_pct),
        "max_error_rate_pct": float(args.canary_max_error_rate_pct),
        "max_consecutive_losses": int(args.canary_max_consecutive_losses),
        "min_observation_trades": int(args.canary_min_observation_trades),
    }
    breaches: List[Dict[str, object]] = []

    if drawdown is not None and drawdown > thresholds["max_drawdown_pct"]:
        breaches.append(
            {
                "metric": "current_drawdown_pct",
                "value": round(drawdown, 4),
                "threshold": thresholds["max_drawdown_pct"],
                "comparison": ">",
                "reason": "drawdown_breach",
            }
        )

    if error_rate is not None and error_rate > thresholds["max_error_rate_pct"]:
        breaches.append(
            {
                "metric": "error_rate_pct",
                "value": round(error_rate, 4),
                "threshold": thresholds["max_error_rate_pct"],
                "comparison": ">",
                "reason": "error_rate_breach",
            }
        )

    if consecutive_losses is not None and consecutive_losses > thresholds["max_consecutive_losses"]:
        breaches.append(
            {
                "metric": "consecutive_losses",
                "value": int(consecutive_losses),
                "threshold": thresholds["max_consecutive_losses"],
                "comparison": ">",
                "reason": "consecutive_losses_breach",
            }
        )

    winrate_check_enforced = (
        observation_trades is None
        or observation_trades >= thresholds["min_observation_trades"]
    )
    if (
        winrate_check_enforced
        and winrate_delta is not None
        and winrate_delta < thresholds["min_win_rate_delta_pct"]
    ):
        breaches.append(
            {
                "metric": "win_rate_delta_pct",
                "value": round(winrate_delta, 4),
                "threshold": thresholds["min_win_rate_delta_pct"],
                "comparison": "<",
                "reason": "winrate_delta_breach",
            }
        )

    metrics_available = any(
        x is not None
        for x in [drawdown, winrate_delta, error_rate, consecutive_losses, observation_trades]
    )
    rollback_required = bool(enabled and breaches)
    primary_reason = breaches[0]["reason"] if breaches else "no_breach"

    affected_rule_ids = [str(p.get("rule_id", "")) for p in proposals if str(p.get("rule_id", ""))]
    actions: List[Dict[str, object]] = []
    if rollback_required:
        actions = [
            {
                "action": "HALT_CANARY_DEPLOYMENT",
                "duration_days": max(1, int(args.rollback_halt_days)),
                "reason": primary_reason,
            },
            {
                "action": "FORCE_MANUAL_REVIEW_MODE",
                "value": "manual",
                "reason": "rollback_triggered",
            },
            {
                "action": "ROLLBACK_ACTIVE_RULES",
                "rule_ids": affected_rule_ids,
                "reason": "risk_limit_breach",
            },
        ]

    return {
        "enabled": enabled,
        "rollback_required": rollback_required,
        "primary_reason": primary_reason,
        "metrics": {
            "current_drawdown_pct": drawdown,
            "current_drawdown_source": drawdown_src,
            "win_rate_delta_pct": winrate_delta,
            "win_rate_delta_source": winrate_src,
            "error_rate_pct": error_rate,
            "error_rate_source": error_src,
            "consecutive_losses": int(consecutive_losses) if consecutive_losses is not None else None,
            "consecutive_losses_source": losses_src,
            "observation_trades": int(observation_trades) if observation_trades is not None else None,
            "observation_trades_source": obs_src,
            "metrics_available": metrics_available,
            "winrate_check_enforced": winrate_check_enforced,
        },
        "metrics_file": {
            "path": str(metrics_path),
            "status": "PASS" if not metrics_err else "MISSING_OR_ERROR",
            "error": metrics_err,
        },
        "thresholds": thresholds,
        "breaches": breaches,
        "actions": actions,
        "affected_rule_ids": affected_rule_ids,
    }


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir)
    symbols = [x.upper() for x in parse_csv_list(args.symbols)]
    if not symbols:
        print("ERROR: symbol allowlist is empty.", file=sys.stderr)
        return 2

    date_str, err = resolve_date(base_dir, args.date.strip())
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2

    date_dir = base_dir / date_str
    if not date_dir.exists():
        print(f"ERROR: date folder not found: {date_dir}", file=sys.stderr)
        return 2

    input_dir = Path(args.input_dir) if args.input_dir.strip() else date_dir
    output_json = Path(args.output_json) if args.output_json.strip() else (date_dir / f"bot_rules_update_{date_str}.json")
    output_json.parent.mkdir(parents=True, exist_ok=True)

    pattern_csv = input_dir / f"pattern_templates_{date_str}.csv"
    run_summary_json = input_dir / f"run_summary_{date_str}.json"
    quality_json = input_dir / f"quality_summary_{date_str}.json"
    validation_json = input_dir / "forensics_file_validation_summary.json"

    _ph, pattern_rows, pattern_err = read_csv(pattern_csv)
    run_summary_payload, run_summary_err = read_json(run_summary_json)
    quality_payload, quality_err = read_json(quality_json)
    validation_payload, validation_err = read_json(validation_json)

    critical_errors: List[str] = []
    if pattern_err:
        critical_errors.append(f"pattern_templates:{pattern_err}")
    if run_summary_err:
        critical_errors.append(f"run_summary:{run_summary_err}")
    if quality_err:
        critical_errors.append(f"quality_summary:{quality_err}")
    if validation_err:
        critical_errors.append(f"validation_summary:{validation_err}")

    allow = set(symbols)
    max_props = max(1, int(args.max_proposals))
    min_conf = float(args.min_confidence)
    min_samples = max(1, int(args.min_sample_count))
    min_hit = float(args.min_hit_rate)
    min_exp = float(args.min_expectancy)
    min_decay = float(args.min_decay_score)

    proposals: List[Dict[str, object]] = []
    dropped = {
        "symbol_not_allowed": 0,
        "min_sample_count": 0,
        "min_hit_rate": 0,
        "min_expectancy": 0,
        "min_decay_score": 0,
        "min_confidence": 0,
    }

    for row in pattern_rows:
        symbol = (row.get("symbol", "") or "").upper()
        if symbol not in allow:
            dropped["symbol_not_allowed"] += 1
            continue

        sample_count = int(to_int(row.get("sample_count", "")) or 0)
        hit_rate = float(to_float(row.get("hit_rate", "")) or 0.0)
        expectancy = float(to_float(row.get("expectancy", "")) or 0.0)
        decay_score = float(to_float(row.get("decay_score", "")) or 0.0)
        avg_score = float(to_float(row.get("avg_score", "")) or 0.0)

        if sample_count < min_samples:
            dropped["min_sample_count"] += 1
            continue
        if hit_rate < min_hit:
            dropped["min_hit_rate"] += 1
            continue
        if expectancy < min_exp:
            dropped["min_expectancy"] += 1
            continue
        if decay_score < min_decay:
            dropped["min_decay_score"] += 1
            continue

        confidence = compute_rule_confidence(sample_count, hit_rate, expectancy, decay_score, avg_score)
        if confidence < min_conf:
            dropped["min_confidence"] += 1
            continue

        trigger_combo = row.get("trigger_combo", "")
        trigger_type, recommendation, vol_state = parse_trigger_combo(trigger_combo)
        regime = row.get("regime", "")
        logic = compose_rule_logic(regime, trigger_type, recommendation, vol_state)

        win_rate_delta = round((hit_rate - 50.0) * 0.2, 2)
        drawdown_delta = round(max(-5.0, min(5.0, -expectancy * 3.5)), 2)
        cost_impact_bps = round(2.0 + min(20.0, sample_count * 0.6), 2)
        if cost_impact_bps <= 6:
            cost_impact = "LOW"
        elif cost_impact_bps <= 14:
            cost_impact = "MEDIUM"
        else:
            cost_impact = "HIGH"

        pattern_id_raw = row.get("pattern_id", "")
        proposals.append(
            {
                "rule_id": rule_id(date_str, symbol, pattern_id_raw),
                "status": "PROPOSED",
                "symbol": symbol,
                "pattern_id": pattern_id_raw,
                "regime": regime,
                "trigger_combo": trigger_combo,
                "sample_count": sample_count,
                "hit_rate": round(hit_rate, 2),
                "expectancy": round(expectancy, 4),
                "decay_score": round(decay_score, 4),
                "confidence_score": round(confidence, 2),
                "logic": logic,
                "expected_impact": {
                    "win_rate_delta_pct": win_rate_delta,
                    "drawdown_delta_pct": drawdown_delta,
                    "cost_impact": cost_impact,
                    "cost_impact_bps": cost_impact_bps,
                    "metric_mode": "proxy_from_pattern_templates",
                },
                "reason": (
                    f"Pattern {pattern_id_raw} observed {sample_count} times with "
                    f"hit_rate={round(hit_rate,2)} and expectancy={round(expectancy,4)} "
                    f"in regime={regime}; trigger={trigger_combo}."
                ),
            }
        )

    proposals.sort(
        key=lambda x: (
            -float(x["confidence_score"]),
            -int(x["sample_count"]),
            -float(x["expectancy"]),
        )
    )
    proposals = proposals[:max_props]

    target_date_obj = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    resolved_approval_start_date = args.approval_start_date.strip()
    approval_start_source = "explicit"
    if not resolved_approval_start_date:
        resolved_approval_start_date, approval_start_source = infer_approval_start_date(base_dir, date_str)

    manual_lock_active = True
    manual_lock_reason = "approval_start_date_not_set"
    days_from_start = None
    if resolved_approval_start_date:
        try:
            start_date_obj = dt.datetime.strptime(resolved_approval_start_date, "%Y-%m-%d").date()
            days_from_start = (target_date_obj - start_date_obj).days + 1
            manual_lock_active = days_from_start <= max(0, int(args.manual_lock_days))
            manual_lock_reason = "within_manual_lock_window" if manual_lock_active else "manual_lock_window_elapsed"
        except ValueError:
            manual_lock_active = True
            manual_lock_reason = "invalid_approval_start_date"
            critical_errors.append("invalid_approval_start_date")
    else:
        manual_lock_active = True
        manual_lock_reason = "approval_start_date_not_available"

    gate1 = evaluate_quality_gate(
        base_dir=base_dir,
        date_str=date_str,
        symbols=symbols,
        lookback_days=max(1, int(args.quality_lookback_days)),
        min_score=float(args.quality_min_score),
    )
    gate2_pass = (
        len(proposals) > 0
        and min(int(p["sample_count"]) for p in proposals) >= int(args.gate2_min_pattern_samples)
    )
    gate2 = {
        "pass": gate2_pass,
        "threshold": int(args.gate2_min_pattern_samples),
        "proposal_count": len(proposals),
        "min_sample_count_in_proposals": min([int(p["sample_count"]) for p in proposals], default=0),
        "reason": "ok" if gate2_pass else "insufficient_pattern_samples",
    }

    gate3 = evaluate_walkforward_gate(args, proposals)
    gate4 = evaluate_drawdown_gate(args, proposals)

    validation_status = str(validation_payload.get("overall_status", "")).upper()
    run_summary_status = str(run_summary_payload.get("overall_status", "")).upper()
    quality_status = str(quality_payload.get("overall_status", "")).upper()
    gate5_pass = (
        validation_status == "PASS"
        and quality_status == "PASS"
        and run_summary_status in ("PASS", "PARTIAL")
    )
    gate5 = {
        "pass": gate5_pass,
        "validation_status": validation_status or "UNKNOWN",
        "quality_status": quality_status or "UNKNOWN",
        "run_summary_status": run_summary_status or "UNKNOWN",
        "reason": "ok" if gate5_pass else "current_day_validation_failed",
    }

    canary_required = int(args.canary_required) == 1
    gate6_pass = (not canary_required) or (args.deployment_mode in ("canary", "paper"))
    gate6 = {
        "pass": gate6_pass,
        "canary_required": canary_required,
        "deployment_mode": args.deployment_mode,
        "reason": "ok" if gate6_pass else "canary_required_but_full_requested",
    }

    guardrails = {
        "gate1_quality_lookback": gate1,
        "gate2_pattern_sample_floor": gate2,
        "gate3_walkforward": gate3,
        "gate4_drawdown_cap": gate4,
        "gate5_current_day_integrity": gate5,
        "gate6_canary_first": gate6,
    }
    all_gates_pass = all(bool(v.get("pass")) for v in guardrails.values())

    if args.approval_mode == "manual":
        auto_approved = False
        approval_reason = "approval_mode_manual"
    elif manual_lock_active:
        auto_approved = False
        approval_reason = f"manual_lock_active:{manual_lock_reason}"
    elif all_gates_pass:
        auto_approved = True
        approval_reason = "all_guardrails_passed"
    else:
        auto_approved = False
        approval_reason = "guardrails_not_satisfied"

    pre_rollback_auto_approved = auto_approved
    rollback_plan = evaluate_canary_rollback(
        args,
        date_dir=date_dir,
        date_str=date_str,
        proposals=proposals,
    )
    rollback_override_reason = ""
    if bool(rollback_plan.get("rollback_required")):
        rollback_override_reason = f"rollback_triggered:{rollback_plan.get('primary_reason', 'risk_breach')}"
        auto_approved = False
        if pre_rollback_auto_approved:
            approval_reason = rollback_override_reason
        else:
            approval_reason = f"{approval_reason};{rollback_override_reason}"

    for p in proposals:
        p["status"] = "APPROVED_CANARY" if auto_approved else "PENDING_REVIEW"
        p["deployment_mode"] = args.deployment_mode if auto_approved else "pending_review"
        if bool(rollback_plan.get("rollback_required")):
            p["rollback_flag"] = "Y"
            p["rollback_reason"] = str(rollback_plan.get("primary_reason", "risk_breach"))

    overall_status = "PASS"
    if critical_errors:
        overall_status = "FAIL"
    elif len(proposals) == 0:
        overall_status = "PASS"

    result = {
        "run_ts": dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST"),
        "base_dir": str(base_dir),
        "date": date_str,
        "validated_symbols": symbols,
        "overall_status": overall_status,
        "input_files": {
            "pattern_templates": str(pattern_csv),
            "run_summary": str(run_summary_json),
            "quality_summary": str(quality_json),
            "validation_summary": str(validation_json),
        },
        "critical_errors": critical_errors,
        "filters": {
            "max_proposals": max_props,
            "min_confidence": min_conf,
            "min_sample_count": min_samples,
            "min_hit_rate": min_hit,
            "min_expectancy": min_exp,
            "min_decay_score": min_decay,
            "dropped_counts": dropped,
        },
        "approval": {
            "requested_mode": args.approval_mode,
            "approval_start_date": args.approval_start_date.strip() or "",
            "resolved_approval_start_date": resolved_approval_start_date,
            "approval_start_source": approval_start_source,
            "manual_lock_days": int(args.manual_lock_days),
            "days_from_start": days_from_start,
            "manual_lock_active": manual_lock_active,
            "manual_lock_reason": manual_lock_reason,
            "pre_rollback_auto_approved": pre_rollback_auto_approved,
            "auto_approved": auto_approved,
            "approval_reason": approval_reason,
            "rollback_override_reason": rollback_override_reason,
            "guardrails": guardrails,
        },
        "canary_rollback": rollback_plan,
        "proposals_generated": len(proposals),
        "proposals": proposals,
        "notes": [
            "Expected impact fields are proxy metrics until trade outcome linkage is integrated.",
            "Gate3 and Gate4 support auto proxy mode when explicit inputs are not provided.",
            "Auto-rollback triggers when configured canary/live metrics breach risk thresholds.",
            "Auto-approval is only applied when mode=auto, manual lock is inactive, and all guardrails pass.",
        ],
    }

    with output_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"[forensics_generate_bot_rules_update] date={date_str}")
    print(f"[forensics_generate_bot_rules_update] validated_symbols={','.join(symbols)}")
    print(
        f"[forensics_generate_bot_rules_update] result={overall_status} "
        f"proposals={len(proposals)} auto_approved={'Y' if auto_approved else 'N'}"
    )
    print(f"[forensics_generate_bot_rules_update] output_json={output_json}")

    if args.fail_on_errors and (critical_errors or overall_status != "PASS"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
