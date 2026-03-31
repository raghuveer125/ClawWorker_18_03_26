#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Add shared_project_engine to path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_SCRIPT_DIR)))
sys.path.insert(0, _PROJECT_ROOT)

# Import market hours from shared config
try:
    from shared_project_engine.market import is_within_buffer_hours as is_market_open, IST
except ImportError:
    # Fallback if shared config not available
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")

    def is_market_open() -> bool:
        """Fallback: Check if Indian market is open (9:00-15:45 IST, Mon-Fri)."""
        now = dt.datetime.now(IST)
        if now.weekday() >= 5:
            return False
        now_mins = now.hour * 60 + now.minute
        return (9 * 60) <= now_mins <= (15 * 60 + 45)


from core.utils import to_float, to_int, parse_dt, ensure_csv, append_csv  # noqa: E402


def load_csv_rows(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_state(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {
            "processed_rows": 0,
            "recent": {},
            "recent_long": {},
            "open_positions": {},
            "last_vote_side": "",
            "last_vote_diff": 0,
            "last_vol_dom": "NEUTRAL",
            "last_reversal_ts": {},
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("invalid state")
        d.setdefault("processed_rows", 0)
        d.setdefault("recent", {})
        d.setdefault("recent_long", {})
        d.setdefault("open_positions", {})
        d.setdefault("last_vote_side", "")
        d.setdefault("last_vote_diff", 0)
        d.setdefault("last_vol_dom", "NEUTRAL")
        d.setdefault("last_reversal_ts", {})
        return d
    except Exception:
        return {
            "processed_rows": 0,
            "recent": {},
            "recent_long": {},
            "open_positions": {},
            "last_vote_side": "",
            "last_vote_diff": 0,
            "last_vol_dom": "NEUTRAL",
            "last_reversal_ts": {},
        }


def save_state(path: str, state: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def prefilter_early_ok(reason: str) -> bool:
    tokens = {x.strip() for x in (reason or "").split(",") if x.strip()}
    if not tokens:
        return False
    allowed = {"rank", "low_conf", "low_delta"}
    return tokens.issubset(allowed)


def score_row(r: Dict[str, str]) -> int:
    conf = to_int(r.get("confidence", "0"), 0)
    vote_diff = to_int(r.get("vote_diff", "0"), 0)
    stable = (r.get("stable", "N") or "N").upper() == "Y"
    flow_match = (r.get("flow_match", "N") or "N").upper() == "Y"
    side = (r.get("side", "") or "").upper()
    vol_dom = (r.get("vol_dom", "") or "").upper()
    spread = to_float(r.get("spread_pct", "0"), 0.0)
    delta = abs(to_float(r.get("delta", "0"), 0.0))
    gamma = to_float(r.get("gamma", "0"), 0.0)
    iv = to_float(r.get("iv", "0"), 0.0)
    net_pcr = to_float(r.get("net_pcr", "0"), 0.0)
    fut_basis_pct = to_float(r.get("fut_basis_pct", "0"), 0.0)
    max_pain_dist = abs(to_float(r.get("max_pain_dist", "0"), 0.0))
    strike_pcr = to_float(r.get("strike_pcr", "0"), 0.0)
    vix = to_float(r.get("vix", "0"), 0.0)
    status = (r.get("status", "") or "").upper()
    action = (r.get("action", "") or "").upper()
    entry_ready = (r.get("entry_ready", "N") or "N").upper() == "Y"

    score = 0

    if vote_diff >= 8:
        score += 24
    elif vote_diff >= 6:
        score += 20
    elif vote_diff >= 4:
        score += 12

    if conf >= 92:
        score += 14
    elif conf >= 88:
        score += 12
    elif conf >= 84:
        score += 8

    if stable:
        score += 10
    if flow_match:
        score += 10

    if vol_dom == side:
        score += 10
    elif vol_dom == "NEUTRAL":
        score += 5

    if spread <= 1.2:
        score += 10
    elif spread <= 2.0:
        score += 7
    elif spread <= 2.8:
        score += 3

    if 0.06 <= delta <= 0.35:
        score += 12
    elif delta > 0.35:
        score += 8
    elif delta >= 0.03:
        score += 4

    if gamma >= 0.0010:
        score += 12
    elif gamma >= 0.0006:
        score += 9
    elif gamma >= 0.00035:
        score += 6

    if 22.0 <= iv <= 55.0:
        score += 8

    # Cross-market/context weighting (only when values exist).
    if vix > 0:
        if 12.0 <= vix <= 25.0:
            score += 2
        elif vix < 9.0 or vix > 35.0:
            score -= 1

    if side == "CE":
        if net_pcr >= 1.10:
            score += 4
        elif net_pcr > 0 and net_pcr <= 0.90:
            score -= 4
        if fut_basis_pct >= 0.15:
            score += 3
        elif fut_basis_pct < -0.05:
            score -= 3
        if max_pain_dist >= 120.0:
            score += 2
        elif max_pain_dist > 0 and max_pain_dist <= 40.0:
            score -= 2
        if strike_pcr >= 1.05:
            score += 2
        elif strike_pcr > 0 and strike_pcr <= 0.80:
            score -= 2
    elif side == "PE":
        if net_pcr <= 0.95 and net_pcr > 0:
            score += 4
        elif net_pcr >= 1.20:
            score -= 4
        if fut_basis_pct <= -0.05:
            score += 3
        elif fut_basis_pct >= 0.20:
            score -= 3
        if max_pain_dist >= 120.0:
            score += 2
        elif max_pain_dist > 0 and max_pain_dist <= 40.0:
            score -= 2
        if strike_pcr <= 0.95 and strike_pcr > 0:
            score += 2
        elif strike_pcr >= 1.20:
            score -= 2

    if status == "APPROVED" and action == "TAKE" and entry_ready:
        score += 8
    elif status == "PREFILTER" and prefilter_early_ok(r.get("reason", "")):
        score += 5

    return max(0, min(100, score))


def momentum_ok(recent: List[Dict[str, float]], entry: float, delta: float, gamma: float) -> bool:
    if not recent:
        return True
    prev = recent[-1]
    prev_entry = to_float(prev.get("entry", 0.0), 0.0)
    prev_delta = to_float(prev.get("delta", 0.0), 0.0)
    prev_gamma = to_float(prev.get("gamma", 0.0), 0.0)

    up_price = prev_entry > 0 and entry >= prev_entry * 1.06
    up_delta = delta >= prev_delta + 0.015
    up_gamma = gamma >= prev_gamma + 0.00012
    return up_price or up_delta or up_gamma


def detect_reversal(
    history: List[Dict[str, float]],
    ts: dt.datetime,
    side: str,
    entry: float,
    delta: float,
    iv: float,
    oich: float,
    vol_oi_ratio: float,
    net_pcr: float,
    fut_basis_pct: float,
    max_pain_dist: float,
    strike_pcr: float,
    conf: int,
    status: str,
    reason: str,
    args: argparse.Namespace,
) -> Tuple[bool, int, str]:
    if not args.enable_reversal:
        return False, 0, ""
    if entry <= 0:
        return False, 0, ""
    if len(history) < max(3, int(args.reversal_min_points)):
        return False, 0, ""

    lookback = history[-max(3, int(args.reversal_lookback)) :]
    peak = max(lookback, key=lambda x: to_float(x.get("entry", 0.0), 0.0))
    peak_entry = to_float(peak.get("entry", 0.0), 0.0)
    peak_delta = to_float(peak.get("delta", 0.0), 0.0)
    peak_iv = to_float(peak.get("iv", 0.0), 0.0)
    peak_ts = parse_dt(peak.get("date", ""), peak.get("time", ""))

    if peak_entry <= 0 or entry >= peak_entry:
        return False, 0, ""
    if (ts - peak_ts).total_seconds() > int(args.reversal_peak_age_sec):
        return False, 0, ""

    drop_pct = ((peak_entry - entry) / peak_entry) * 100.0
    delta_drop = max(0.0, peak_delta - delta)
    iv_drop = max(0.0, peak_iv - iv)

    reason_tokens = {x.strip() for x in (reason or "").split(",") if x.strip()}
    quality_deterioration = (
        conf <= int(args.reversal_conf_floor)
        or status == "PREFILTER"
        or bool(reason_tokens.intersection({"low_conf", "low_delta", "unstable_side"}))
    )

    hit = (
        drop_pct >= float(args.reversal_drop_pct)
        and delta_drop >= float(args.reversal_delta_drop)
        and iv_drop >= float(args.reversal_iv_drop)
        and quality_deterioration
    )
    flow_ok = (
        oich >= float(args.reversal_min_oich)
        and vol_oi_ratio >= float(args.reversal_min_vol_oi_ratio)
    )
    if args.reversal_require_flow and not flow_ok:
        return False, 0, ""
    if not hit:
        return False, 0, ""

    context_boost = 0.0
    # Optional context weighting from VIX/PCR/MaxPain/Futures basis.
    if abs(max_pain_dist) > 0 and abs(max_pain_dist) <= float(args.reversal_maxpain_band):
        context_boost += 4.0
    if side == "CE":
        if fut_basis_pct <= float(args.reversal_basis_pct_ce_max):
            context_boost += 3.0
        if strike_pcr > 0 and strike_pcr <= float(args.reversal_strike_pcr_ce_max):
            context_boost += 2.0
        if net_pcr > 0 and net_pcr <= float(args.reversal_net_pcr_ce_max):
            context_boost += 2.0

    if side == "CE" and args.reversal_require_context and context_boost < 4.0:
        return False, 0, ""

    flow_boost = 8.0 if flow_ok else 0.0
    score = int(
        min(
            100.0,
            (drop_pct * 0.9) + (delta_drop * 120.0) + (iv_drop * 0.9) + flow_boost + context_boost,
        )
    )
    detail = (
        f"drop={drop_pct:.1f}% delta_drop={delta_drop:.3f} "
        f"iv_drop={iv_drop:.2f} oich={oich:.0f} vor={vol_oi_ratio:.2f} "
        f"pcr={net_pcr:.2f} basis%={fut_basis_pct:.2f} mpd={max_pain_dist:.1f} "
        f"peak={peak_entry:.2f}@{peak_ts.strftime('%H:%M:%S')}"
    )
    return True, score, detail


def run_pull(args: argparse.Namespace) -> int:
    if args.no_pull:
        return 0

    cmd = [
        sys.executable,
        args.pull_script,
        "--only-approved",
        "--insecure",
        "--profile",
        args.profile,
        "--ladder-count",
        str(args.ladder_count),
        "--otm-start",
        str(args.otm_start),
        "--max-premium",
        str(args.max_premium),
        "--min-premium",
        str(args.min_premium),
        "--min-confidence",
        str(args.min_confidence),
        "--min-score",
        str(args.min_score),
        "--min-abs-delta",
        str(args.min_abs_delta),
        "--min-vote-diff",
        str(args.min_vote_diff),
        "--adaptive-model-file",
        args.adaptive_model_file,
        "--min-learn-prob",
        str(args.min_learn_prob),
        "--min-model-samples",
        str(args.min_model_samples),
        "--journal-csv",
        args.journal_csv,
        "--confirm-pulls",
        str(args.confirm_pulls),
        "--flip-cooldown-sec",
        str(args.flip_cooldown_sec),
        "--max-select-strikes",
        str(args.max_select_strikes),
        "--max-spread-pct",
        str(args.max_spread_pct),
        "--state-file",
        args.signal_state_file,
    ]

    if args.enable_adaptive:
        cmd.append("--enable-adaptive")
    if args.show_signal_table:
        cmd.append("--table")

    p = subprocess.run(cmd, capture_output=not args.show_signal_table, text=True)
    if p.returncode != 0 and p.stderr:
        print(p.stderr.strip())
    return p.returncode


def print_table(headers: List[str], rows: List[List[str]]) -> None:
    if not rows:
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def fmt(row: List[str]) -> str:
        return " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))

    print(fmt(headers))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(fmt(row))


def process_rows(
    rows: List[Dict[str, str]],
    state: Dict[str, Any],
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], int]:
    processed = to_int(state.get("processed_rows", 0), 0)
    if processed > len(rows):
        processed = 0
    new_rows = rows[processed:]
    state["processed_rows"] = len(rows)

    events_headers = [
        "event_date",
        "event_time",
        "event_type",
        "side",
        "strike",
        "entry",
        "exit",
        "sl",
        "t1",
        "t2",
        "score",
        "confidence",
        "vote_diff",
        "delta",
        "gamma",
        "spread_pct",
        "vix",
        "net_pcr",
        "max_pain_dist",
        "fut_basis_pct",
        "strike_pcr",
        "reason",
    ]

    entries: List[Dict[str, Any]] = []
    exits: List[Dict[str, Any]] = []
    reversals: List[Dict[str, Any]] = []

    recent: Dict[str, List[Dict[str, float]]] = state.get("recent", {}) or {}
    recent_long: Dict[str, List[Dict[str, float]]] = state.get("recent_long", {}) or {}
    open_positions: Dict[str, Dict[str, Any]] = state.get("open_positions", {}) or {}
    last_reversal_ts: Dict[str, str] = state.get("last_reversal_ts", {}) or {}

    for r in new_rows:
        side = (r.get("side", "") or "").upper()
        strike = (r.get("strike", "") or "")
        if side not in {"CE", "PE"} or not strike:
            continue

        ts = parse_dt(r.get("date", ""), r.get("time", ""))
        key = f"{side}:{strike}"

        entry = to_float(r.get("entry", "0"), 0.0)
        sl = to_float(r.get("sl", "0"), 0.0)
        t1 = to_float(r.get("t1", "0"), 0.0)
        t2 = to_float(r.get("t2", "0"), 0.0)
        delta = abs(to_float(r.get("delta", "0"), 0.0))
        gamma = to_float(r.get("gamma", "0"), 0.0)
        iv = to_float(r.get("iv", "0"), 0.0)
        oich = to_float(r.get("oich", "0"), 0.0)
        vix = to_float(r.get("vix", "0"), 0.0)
        vol_oi_ratio = to_float(r.get("vol_oi_ratio", "0"), 0.0)
        net_pcr = to_float(r.get("net_pcr", "0"), 0.0)
        fut_basis_pct = to_float(r.get("fut_basis_pct", "0"), 0.0)
        max_pain_dist = to_float(r.get("max_pain_dist", "0"), 0.0)
        strike_pcr = to_float(r.get("strike_pcr", "0"), 0.0)
        spread = to_float(r.get("spread_pct", "0"), 0.0)

        state["last_vote_side"] = (r.get("vote_side", "") or "")
        state["last_vote_diff"] = to_int(r.get("vote_diff", "0"), 0)
        state["last_vol_dom"] = (r.get("vol_dom", "") or "NEUTRAL")

        h = recent.get(key, [])
        h_long = recent_long.get(key, [])
        m_ok = momentum_ok(h, entry, delta, gamma)
        s = score_row(r)

        status = (r.get("status", "") or "").upper()
        action = (r.get("action", "") or "").upper()
        stable = (r.get("stable", "N") or "N").upper() == "Y"
        flow_match = (r.get("flow_match", "N") or "N").upper() == "Y"
        vote_diff = to_int(r.get("vote_diff", "0"), 0)
        conf = to_int(r.get("confidence", "0"), 0)

        allow_prefilter = status == "PREFILTER" and prefilter_early_ok(r.get("reason", ""))
        allow_approved = status == "APPROVED" and action == "TAKE"

        should_enter = (
            key not in open_positions
            and entry > 0
            and s >= int(args.min_entry_score)
            and m_ok
            and stable
            and flow_match
            and vote_diff >= int(args.min_vote_diff_entry)
            and spread <= float(args.max_spread_entry)
            and (allow_approved or allow_prefilter)
            and conf >= int(args.min_conf_entry)
        )

        if should_enter:
            open_positions[key] = {
                "key": key,
                "side": side,
                "strike": strike,
                "entry": entry,
                "sl": sl,
                "t1": t1,
                "t2": t2,
                "opened_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "last_price": entry,
                "last_ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "score": s,
            }
            ev = {
                "event_date": ts.strftime("%Y-%m-%d"),
                "event_time": ts.strftime("%H:%M:%S"),
                "event_type": "ENTRY",
                "side": side,
                "strike": strike,
                "entry": f"{entry:.2f}",
                "exit": "",
                "sl": f"{sl:.2f}",
                "t1": f"{t1:.2f}",
                "t2": f"{t2:.2f}",
                "score": str(s),
                "confidence": str(conf),
                "vote_diff": str(vote_diff),
                "delta": f"{delta:.4f}",
                "gamma": f"{gamma:.6f}",
                "spread_pct": f"{spread:.3f}",
                "vix": f"{vix:.2f}" if vix > 0 else "",
                "net_pcr": f"{net_pcr:.4f}" if net_pcr > 0 else "",
                "max_pain_dist": f"{max_pain_dist:.2f}",
                "fut_basis_pct": f"{fut_basis_pct:.4f}",
                "strike_pcr": f"{strike_pcr:.4f}" if strike_pcr > 0 else "",
                "reason": "entry_signal",
            }
            entries.append(ev)
            append_csv(args.events_csv, events_headers, ev)

        if key in open_positions:
            p = open_positions[key]
            p["last_price"] = entry
            p["last_ts"] = ts.strftime("%Y-%m-%d %H:%M:%S")

            opened_at = dt.datetime.strptime(p["opened_at"], "%Y-%m-%d %H:%M:%S")
            age_sec = max(0, int((ts - opened_at).total_seconds()))

            exit_reason = ""
            exit_px = entry
            if entry <= to_float(p.get("sl", 0.0), 0.0):
                exit_reason = "SL"
            elif entry >= to_float(p.get("t2", 0.0), 0.0) > 0:
                exit_reason = "T2"
            elif entry >= to_float(p.get("t1", 0.0), 0.0) > 0:
                exit_reason = "T1"
            elif args.exit_on_flip:
                vote_side = (state.get("last_vote_side", "") or "")
                vote_diff_now = to_int(state.get("last_vote_diff", 0), 0)
                vol_dom = (state.get("last_vol_dom", "") or "").upper()
                if vote_side in {"CE", "PE"} and vote_side != side and vote_diff_now >= int(args.flip_vote_diff):
                    if vol_dom == vote_side:
                        exit_reason = "SIDE_FLIP"
            if not exit_reason and args.max_hold_sec > 0 and age_sec >= int(args.max_hold_sec):
                exit_reason = "TIME"

            if exit_reason:
                ev = {
                    "event_date": ts.strftime("%Y-%m-%d"),
                    "event_time": ts.strftime("%H:%M:%S"),
                    "event_type": "EXIT",
                    "side": side,
                    "strike": strike,
                    "entry": f"{to_float(p.get('entry', 0.0)):.2f}",
                    "exit": f"{exit_px:.2f}",
                    "sl": f"{to_float(p.get('sl', 0.0)):.2f}",
                    "t1": f"{to_float(p.get('t1', 0.0)):.2f}",
                    "t2": f"{to_float(p.get('t2', 0.0)):.2f}",
                    "score": str(to_int(p.get("score", 0), 0)),
                    "confidence": str(conf),
                    "vote_diff": str(vote_diff),
                    "delta": f"{delta:.4f}",
                    "gamma": f"{gamma:.6f}",
                    "spread_pct": f"{spread:.3f}",
                    "vix": f"{vix:.2f}" if vix > 0 else "",
                    "net_pcr": f"{net_pcr:.4f}" if net_pcr > 0 else "",
                    "max_pain_dist": f"{max_pain_dist:.2f}",
                    "fut_basis_pct": f"{fut_basis_pct:.4f}",
                    "strike_pcr": f"{strike_pcr:.4f}" if strike_pcr > 0 else "",
                    "reason": exit_reason,
                }
                exits.append(ev)
                append_csv(args.events_csv, events_headers, ev)
                del open_positions[key]

        h.append({"entry": entry, "delta": delta, "gamma": gamma})
        recent[key] = h[-3:]
        h_long.append(
            {
                "date": ts.strftime("%Y-%m-%d"),
                "time": ts.strftime("%H:%M:%S"),
                "side": side,
                "entry": entry,
                "delta": delta,
                "gamma": gamma,
                "iv": iv,
                "conf": conf,
            }
        )
        recent_long[key] = h_long[-max(10, int(args.reversal_lookback) + 5) :]

        rev_hit, rev_score, rev_detail = detect_reversal(
            history=recent_long[key],
            ts=ts,
            side=side,
            entry=entry,
            delta=delta,
            iv=iv,
            oich=oich,
            vol_oi_ratio=vol_oi_ratio,
            net_pcr=net_pcr,
            fut_basis_pct=fut_basis_pct,
            max_pain_dist=max_pain_dist,
            strike_pcr=strike_pcr,
            conf=conf,
            status=status,
            reason=r.get("reason", ""),
            args=args,
        )
        if rev_hit:
            prev_rev = last_reversal_ts.get(key, "")
            prev_ts = parse_dt(ts.strftime("%Y-%m-%d"), prev_rev) if prev_rev else None
            cooldown_ok = (
                prev_ts is None
                or (ts - prev_ts).total_seconds() >= int(args.reversal_cooldown_sec)
            )
            if cooldown_ok:
                last_reversal_ts[key] = ts.strftime("%H:%M:%S")
                rev_ev = {
                    "event_date": ts.strftime("%Y-%m-%d"),
                    "event_time": ts.strftime("%H:%M:%S"),
                    "event_type": "REVERSAL",
                    "side": side,
                    "strike": strike,
                    "entry": f"{entry:.2f}",
                    "exit": "",
                    "sl": f"{sl:.2f}",
                    "t1": f"{t1:.2f}",
                    "t2": f"{t2:.2f}",
                    "score": str(rev_score),
                    "confidence": str(conf),
                    "vote_diff": str(vote_diff),
                    "delta": f"{delta:.4f}",
                    "gamma": f"{gamma:.6f}",
                    "spread_pct": f"{spread:.3f}",
                    "vix": f"{vix:.2f}" if vix > 0 else "",
                    "net_pcr": f"{net_pcr:.4f}" if net_pcr > 0 else "",
                    "max_pain_dist": f"{max_pain_dist:.2f}",
                    "fut_basis_pct": f"{fut_basis_pct:.4f}",
                    "strike_pcr": f"{strike_pcr:.4f}" if strike_pcr > 0 else "",
                    "reason": f"reversal:{rev_detail}",
                }
                reversals.append(rev_ev)
                append_csv(args.events_csv, events_headers, rev_ev)

                if args.exit_on_reversal and key in open_positions:
                    p = open_positions[key]
                    ev = {
                        "event_date": ts.strftime("%Y-%m-%d"),
                        "event_time": ts.strftime("%H:%M:%S"),
                        "event_type": "EXIT",
                        "side": side,
                        "strike": strike,
                        "entry": f"{to_float(p.get('entry', 0.0)):.2f}",
                        "exit": f"{entry:.2f}",
                        "sl": f"{to_float(p.get('sl', 0.0)):.2f}",
                        "t1": f"{to_float(p.get('t1', 0.0)):.2f}",
                        "t2": f"{to_float(p.get('t2', 0.0)):.2f}",
                        "score": str(to_int(p.get("score", 0), 0)),
                        "confidence": str(conf),
                        "vote_diff": str(vote_diff),
                        "delta": f"{delta:.4f}",
                        "gamma": f"{gamma:.6f}",
                        "spread_pct": f"{spread:.3f}",
                        "vix": f"{vix:.2f}" if vix > 0 else "",
                        "net_pcr": f"{net_pcr:.4f}" if net_pcr > 0 else "",
                        "max_pain_dist": f"{max_pain_dist:.2f}",
                        "fut_basis_pct": f"{fut_basis_pct:.4f}",
                        "strike_pcr": f"{strike_pcr:.4f}" if strike_pcr > 0 else "",
                        "reason": "REVERSAL_EXIT",
                    }
                    exits.append(ev)
                    append_csv(args.events_csv, events_headers, ev)
                    del open_positions[key]

    state["recent"] = recent
    state["recent_long"] = recent_long
    state["open_positions"] = open_positions
    state["last_reversal_ts"] = last_reversal_ts
    return entries, exits, reversals, len(new_rows)


def print_cycle(
    state: Dict[str, Any],
    entries: List[Dict[str, Any]],
    exits: List[Dict[str, Any]],
    reversals: List[Dict[str, Any]],
    seen_rows: int,
    latest_rows: List[Dict[str, str]] = None,
) -> None:
    # Only print when there are events or open positions
    open_count = len(state.get("open_positions", {}))
    has_events = entries or exits or reversals or open_count > 0

    if not has_events:
        # Silent when no events - signal output shows all context
        return

    # Compact summary line (only when there are events)
    print(
        f">>> Opp: Seen:{seen_rows} | Open:{open_count} | "
        f"Entry:{len(entries)} | Exit:{len(exits)} | Reversal:{len(reversals)}"
    )

    if entries:
        print("New Entries")
        rows = [
            [
                e["event_time"],
                e["side"],
                e["strike"],
                e["entry"],
                e["sl"],
                e["t1"],
                e["score"],
                e["confidence"],
                e["vote_diff"],
                e["delta"],
                e["reason"],
            ]
            for e in entries
        ]
        print_table(["Time", "Side", "Strike", "Entry", "SL", "T1", "Score", "Conf", "Vote", "Delta", "Reason"], rows)

    if exits:
        print("New Exits")
        rows = [
            [
                e["event_time"],
                e["side"],
                e["strike"],
                e["entry"],
                e["exit"],
                e["reason"],
            ]
            for e in exits
        ]
        print_table(["Time", "Side", "Strike", "Entry", "Exit", "Reason"], rows)

    if reversals:
        print("Reversal Alerts")
        rows = [
            [
                e["event_time"],
                e["side"],
                e["strike"],
                e["entry"],
                e["score"],
                e["confidence"],
                e["delta"],
                e["reason"],
            ]
            for e in reversals
        ]
        print_table(["Time", "Side", "Strike", "Entry", "RScore", "Conf", "Delta", "Reason"], rows)

    opens = list((state.get("open_positions", {}) or {}).values())
    if opens:
        print("Open Signals:")
        for p in opens[:5]:
            e = to_float(p.get("entry", 0.0), 0.0)
            l = to_float(p.get("last_price", e), e)
            sl = to_float(p.get("sl", 0.0), 0.0)
            t1 = to_float(p.get("t1", 0.0), 0.0)
            pnl = l - e
            pnl_pct = (pnl / e * 100) if e > 0 else 0
            print(
                f"  {p.get('side', '')}{p.get('strike', '')} "
                f"Entry:{e:.1f} LTP:{l:.1f} PnL:{pnl:+.1f}({pnl_pct:+.1f}%) "
                f"SL:{sl:.1f} T1:{t1:.1f}"
            )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Opportunity engine for entry/exit detection from journal data.")

    p.add_argument("--interval-sec", type=int, default=15)
    p.add_argument("--once", action="store_true")

    p.add_argument("--journal-csv", default="decision_journal.csv")
    p.add_argument("--events-csv", default="opportunity_events.csv")
    p.add_argument("--state-file", default=".opportunity_engine_state.json")
    p.add_argument("--start-from-latest", dest="start_from_latest", action="store_true")
    p.add_argument("--start-from-beginning", dest="start_from_latest", action="store_false")
    p.set_defaults(start_from_latest=True)

    p.add_argument("--min-entry-score", type=int, default=74)
    p.add_argument("--min-conf-entry", type=int, default=84)
    p.add_argument("--min-vote-diff-entry", type=int, default=5)
    p.add_argument("--max-spread-entry", type=float, default=2.2)

    p.add_argument("--max-hold-sec", type=int, default=180)
    p.add_argument("--exit-on-flip", dest="exit_on_flip", action="store_true")
    p.add_argument("--no-exit-on-flip", dest="exit_on_flip", action="store_false")
    p.set_defaults(exit_on_flip=True)
    p.add_argument("--flip-vote-diff", type=int, default=5)
    p.add_argument("--enable-reversal", dest="enable_reversal", action="store_true")
    p.add_argument("--disable-reversal", dest="enable_reversal", action="store_false")
    p.set_defaults(enable_reversal=True)
    p.add_argument("--exit-on-reversal", dest="exit_on_reversal", action="store_true")
    p.add_argument("--no-exit-on-reversal", dest="exit_on_reversal", action="store_false")
    p.set_defaults(exit_on_reversal=True)
    p.add_argument("--reversal-lookback", type=int, default=20)
    p.add_argument("--reversal-min-points", type=int, default=6)
    p.add_argument("--reversal-drop-pct", type=float, default=55.0)
    p.add_argument("--reversal-delta-drop", type=float, default=0.12)
    p.add_argument("--reversal-iv-drop", type=float, default=8.0)
    p.add_argument("--reversal-min-oich", type=float, default=0.0)
    p.add_argument("--reversal-min-vol-oi-ratio", type=float, default=0.0)
    p.add_argument("--reversal-require-flow", action="store_true")
    p.add_argument("--reversal-require-context", action="store_true")
    p.add_argument("--reversal-basis-pct-ce-max", type=float, default=0.35)
    p.add_argument("--reversal-maxpain-band", type=float, default=90.0)
    p.add_argument("--reversal-strike-pcr-ce-max", type=float, default=1.10)
    p.add_argument("--reversal-net-pcr-ce-max", type=float, default=1.25)
    p.add_argument("--reversal-peak-age-sec", type=int, default=1800)
    p.add_argument("--reversal-cooldown-sec", type=int, default=120)
    p.add_argument("--reversal-conf-floor", type=int, default=88)

    p.add_argument("--no-pull", action="store_true")
    p.add_argument("--pull-script", default="scripts/pull_fyers_signal.py")
    p.add_argument("--show-signal-table", action="store_true")

    p.add_argument("--profile", default="expiry")
    p.add_argument("--ladder-count", type=int, default=6)
    p.add_argument("--otm-start", type=int, default=1)
    p.add_argument("--max-premium", type=float, default=250)
    p.add_argument("--min-premium", type=float, default=0)
    p.add_argument("--min-confidence", type=int, default=88)
    p.add_argument("--min-score", type=int, default=95)
    p.add_argument("--min-abs-delta", type=float, default=0.10)
    p.add_argument("--min-vote-diff", type=int, default=2)
    p.add_argument("--adaptive-model-file", default=".adaptive_model.json")
    p.add_argument("--enable-adaptive", dest="enable_adaptive", action="store_true")
    p.add_argument("--disable-adaptive", dest="enable_adaptive", action="store_false")
    p.set_defaults(enable_adaptive=True)
    p.add_argument("--min-learn-prob", type=float, default=0.55)
    p.add_argument("--min-model-samples", type=int, default=20)
    p.add_argument("--confirm-pulls", type=int, default=2)
    p.add_argument("--flip-cooldown-sec", type=int, default=45)
    p.add_argument("--max-select-strikes", type=int, default=4)
    p.add_argument("--max-spread-pct", type=float, default=2.5)
    p.add_argument("--signal-state-file", default=".signal_state.json")
    p.add_argument("--skip-market-check", action="store_true",
                   help="Skip market hours check (run even when market is closed)")

    return p


def main() -> int:
    args = build_parser().parse_args()

    state_exists = os.path.exists(args.state_file)
    state = load_state(args.state_file)
    if (not state_exists) and args.start_from_latest:
        rows_now = load_csv_rows(args.journal_csv)
        state["processed_rows"] = len(rows_now)
        state["open_positions"] = {}

    print("Starting opportunity engine")
    print(
        f"Interval: {args.interval_sec}s | EntryScore>={args.min_entry_score} "
        f"Conf>={args.min_conf_entry} VoteDiff>={args.min_vote_diff_entry} Spread<={args.max_spread_entry}%"
    )
    print(
        f"Exit: SL/T1/T2 + flip={'Y' if args.exit_on_flip else 'N'} + time={args.max_hold_sec}s"
    )
    print(
        f"Reversal: {'Y' if args.enable_reversal else 'N'} "
        f"drop>={args.reversal_drop_pct}% delta_drop>={args.reversal_delta_drop} "
        f"iv_drop>={args.reversal_iv_drop} flow_req={'Y' if args.reversal_require_flow else 'N'} "
        f"ctx_req={'Y' if args.reversal_require_context else 'N'} "
        f"exit_on_reversal={'Y' if args.exit_on_reversal else 'N'}"
    )
    print("Press Ctrl+C to stop.")

    last_market_closed_msg = 0
    try:
        while True:
            # Check if market is open
            if not args.skip_market_check and not is_market_open():
                now_ts = int(time.time())
                if now_ts - last_market_closed_msg >= 60:
                    print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Market closed. Waiting...")
                    last_market_closed_msg = now_ts
                time.sleep(max(1, int(args.interval_sec)))
                continue

            print()
            print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Scan cycle...")
            run_pull(args)
            rows = load_csv_rows(args.journal_csv)
            entries, exits, reversals, seen_rows = process_rows(rows, state, args)
            # Pass the latest few rows to show market context
            latest_rows = rows[-5:] if rows else []
            print_cycle(state, entries, exits, reversals, seen_rows, latest_rows)
            save_state(args.state_file, state)
            if args.once:
                break
            time.sleep(max(1, int(args.interval_sec)))
    except KeyboardInterrupt:
        save_state(args.state_file, state)
        print("\nStopped. State saved.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
