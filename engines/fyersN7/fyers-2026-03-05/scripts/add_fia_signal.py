#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import os
import re
import sys
from typing import List, Tuple

HEADERS = [
    "date",
    "time",
    "symbol",
    "side",
    "strike",
    "entry",
    "sl",
    "t1",
    "t2",
    "invalidation",
    "confidence",
    "reason",
    "trend_match",
    "oi_volume_support",
    "spread_ok",
    "final_action",
    "result",
    "notes",
]

INDEX_ALIASES = {
    "SENSEX": "SENSEX",
    "BSESENSEX": "SENSEX",
    "NIFTY": "NIFTY50",
    "NIFTY50": "NIFTY50",
    "BANKNIFTY": "BANKNIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
}


def normalize_index_symbol(symbol: str) -> str:
    key = re.sub(r"[^A-Z0-9]", "", (symbol or "").upper())
    return INDEX_ALIASES.get(key, "")


def normalize_spaces(text: str) -> str:
    return " ".join(text.strip().split())


def to_float(label: str, value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{label.upper()} must be numeric.") from exc


def parse_fia_line(line: str) -> dict:
    raw = normalize_spaces(line)
    if not raw:
        raise ValueError("Input is empty.")

    lower = raw.lower()
    if lower.startswith("no trade"):
        reason = ""
        if "|" in raw:
            parts = [p.strip() for p in raw.split("|", maxsplit=1)]
            if len(parts) == 2:
                reason = parts[1]
        if not reason:
            reason = "No valid setup from FIA"

        now = dt.datetime.now()
        default_symbol = normalize_index_symbol(os.getenv("INDEX", "SENSEX")) or "SENSEX"
        return {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "symbol": default_symbol,
            "side": "",
            "strike": "",
            "entry": "",
            "sl": "",
            "t1": "",
            "t2": "",
            "invalidation": "",
            "confidence": "",
            "reason": reason,
            "trend_match": "",
            "oi_volume_support": "",
            "spread_ok": "",
            "final_action": "Skip",
            "result": "No Trade",
            "notes": "FIA no trade",
        }

    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 12:
        raise ValueError(
            "Expected 12 fields separated by '|'. "
            f"Received {len(parts)} field(s)."
        )

    date, time_, symbol, side, strike, entry, sl, t1, t2, invalidation, confidence, reason = parts

    symbol_norm = normalize_index_symbol(symbol)
    if not symbol_norm:
        raise ValueError("Symbol must be one of: SENSEX, NIFTY50, BANKNIFTY, FINNIFTY, MIDCPNIFTY.")
    symbol = symbol_norm

    side = side.upper()
    if side not in {"CE", "PE"}:
        raise ValueError("SIDE must be CE or PE.")

    _ = to_float("strike", strike)
    _ = to_float("entry", entry)
    _ = to_float("sl", sl)
    _ = to_float("t1", t1)
    _ = to_float("t2", t2)

    try:
        conf_int = int(confidence)
    except ValueError as exc:
        raise ValueError("CONFIDENCE must be integer 0-100.") from exc

    if conf_int < 0 or conf_int > 100:
        raise ValueError("CONFIDENCE must be between 0 and 100.")

    return {
        "date": date,
        "time": time_,
        "symbol": symbol,
        "side": side,
        "strike": strike,
        "entry": entry,
        "sl": sl,
        "t1": t1,
        "t2": t2,
        "invalidation": invalidation,
        "confidence": str(conf_int),
        "reason": reason,
        "trend_match": "",
        "oi_volume_support": "",
        "spread_ok": "",
        "final_action": "",
        "result": "",
        "notes": "",
    }


def quality_assessment(row: dict, min_score: int = 80) -> Tuple[str, int, List[str], dict]:
    # NO TRADE rows are informational and not score-based signals.
    if row["result"] == "No Trade":
        return "NO_TRADE", 0, ["FIA returned no setup"], {
            "risk": 0.0,
            "risk_pct": 0.0,
            "rr1": 0.0,
            "rr2": 0.0,
        }

    entry = to_float("entry", row["entry"])
    sl = to_float("sl", row["sl"])
    t1 = to_float("t1", row["t1"])
    t2 = to_float("t2", row["t2"])
    confidence = int(row["confidence"])

    issues: List[str] = []
    warnings: List[str] = []

    if entry <= 0 or sl <= 0 or t1 <= 0 or t2 <= 0:
        issues.append("Entry/SL/targets must be > 0")

    if sl >= entry:
        issues.append("SL must be lower than entry")
    if t1 <= entry:
        issues.append("T1 must be greater than entry")
    if t2 < t1:
        issues.append("T2 must be >= T1")

    if issues:
        return "REJECTED", 0, issues, {
            "risk": 0.0,
            "risk_pct": 0.0,
            "rr1": 0.0,
            "rr2": 0.0,
        }

    risk = entry - sl
    reward1 = t1 - entry
    reward2 = t2 - entry
    rr1 = reward1 / risk if risk > 0 else 0.0
    rr2 = reward2 / risk if risk > 0 else 0.0
    risk_pct = (risk / entry) * 100 if entry > 0 else 0.0

    score = confidence
    if confidence < 80:
        warnings.append("Confidence < 80")
        score -= 12

    if rr1 < 1.0:
        warnings.append("RR to T1 < 1.0")
        score -= 8
    else:
        score += 4

    if rr2 < 1.5:
        warnings.append("RR to T2 < 1.5")
        score -= 10
    else:
        score += 6

    if risk_pct < 3.0:
        warnings.append("SL too tight (< 3%)")
        score -= 6
    elif risk_pct > 15.0:
        warnings.append("SL too wide (> 15%)")
        score -= 8
    else:
        score += 4

    score = max(0, min(100, score))
    status = "APPROVED" if score >= min_score and not warnings else "REJECTED"
    notes = warnings if warnings else ["All quality checks passed"]
    return status, score, notes, {
        "risk": risk,
        "risk_pct": risk_pct,
        "rr1": rr1,
        "rr2": rr2,
    }


def ensure_csv(csv_path: str) -> None:
    if os.path.exists(csv_path):
        return
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()


def append_row(csv_path: str, row: dict) -> None:
    ensure_csv(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writerow(row)


def format_signal_card(
    row: dict, status: str, score: int, notes: List[str], metrics: dict
) -> str:
    if status == "NO_TRADE":
        lines = [
            "FIA SIGNAL",
            "Status       : NO TRADE",
            f"Time         : {row['date']} {row['time']}",
            f"Reason       : {row['reason']}",
            f"CSV Action   : {row['final_action']}",
        ]
        return "\n".join(lines)

    lines = [
        "FIA SIGNAL",
        f"Status       : {status}",
        f"QualityScore : {score}/100",
        f"Time         : {row['date']} {row['time']}",
        f"Instrument   : {row['symbol']} {row['side']} {row['strike']}",
        f"Entry / SL   : {row['entry']} / {row['sl']} (Risk {metrics['risk']:.2f}, {metrics['risk_pct']:.2f}%)",
        f"Targets      : T1 {row['t1']} (RR {metrics['rr1']:.2f}), T2 {row['t2']} (RR {metrics['rr2']:.2f})",
        f"Invalidation : {row['invalidation']}",
        f"Confidence   : {row['confidence']}",
        f"Reason       : {row['reason']}",
        f"Checks       : {'; '.join(notes)}",
        f"CSV Action   : {row['final_action']}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate FIA signal, print formatted card, append to signals.csv"
    )
    parser.add_argument(
        "line",
        nargs="?",
        help="FIA output line. If omitted, the script reads from stdin.",
    )
    parser.add_argument(
        "--csv",
        default="signals.csv",
        help="CSV file path (default: signals.csv)",
    )
    parser.add_argument(
        "--only-approved",
        action="store_true",
        help="Append only APPROVED signals.",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=80,
        help="Minimum quality score required for APPROVED (default: 80).",
    )
    args = parser.parse_args()

    line = args.line
    if line is None:
        line = sys.stdin.read().strip()

    if not line:
        print("Error: provide FIA line as argument or stdin.", file=sys.stderr)
        return 1

    try:
        row = parse_fia_line(line)
        min_score = max(0, min(100, int(args.min_score)))
        status, score, notes, metrics = quality_assessment(row, min_score=min_score)

        if status == "NO_TRADE":
            row["notes"] = "FIA no trade"
            row["final_action"] = "Skip"
        elif status == "APPROVED":
            row["final_action"] = "Take"
            row["notes"] = (
                f"status={status};score={score};rr1={metrics['rr1']:.2f};"
                f"rr2={metrics['rr2']:.2f};risk_pct={metrics['risk_pct']:.2f}"
            )
        else:
            row["final_action"] = "Skip"
            row["notes"] = (
                f"status={status};score={score};"
                f"checks={'; '.join(notes)};rr1={metrics['rr1']:.2f};"
                f"rr2={metrics['rr2']:.2f};risk_pct={metrics['risk_pct']:.2f}"
            )

        should_save = (status == "APPROVED") or (not args.only_approved)
        if should_save:
            append_row(args.csv, row)

        print(format_signal_card(row, status, score, notes, metrics))
        if should_save:
            print(f"\nSaved: {args.csv}")
        else:
            print(f"\nNot saved (--only-approved enabled, status={status})")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
