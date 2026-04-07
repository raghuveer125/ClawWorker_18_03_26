#!/usr/bin/env python3
"""Deep analysis of paper trades to identify improvement opportunities."""
import argparse
import csv
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Tuple


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
from core.utils import to_float, to_int  # noqa: E402


def load_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def print_section(title: str) -> None:
    print()
    print("=" * 60)
    print(f" {title}")
    print("=" * 60)


def print_table(headers: List[str], rows: List[List[str]], align: str = "left") -> None:
    if not rows:
        print("  (no data)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))

    def fmt(row: List[str]) -> str:
        parts = []
        for i, cell in enumerate(row):
            w = widths[i] if i < len(widths) else len(str(cell))
            parts.append(str(cell).ljust(w) if align == "left" else str(cell).rjust(w))
        return " | ".join(parts)

    print("  " + fmt(headers))
    print("  " + "-+-".join("-" * w for w in widths))
    for row in rows:
        print("  " + fmt(row))


def analyze_trades(trades: List[Dict[str, str]]) -> Dict[str, Any]:
    """Comprehensive trade analysis."""
    results: Dict[str, Any] = {}

    if not trades:
        return {"error": "No trades to analyze"}

    # Basic counts
    total = len(trades)
    wins = [t for t in trades if t.get("result") == "Win"]
    losses = [t for t in trades if t.get("result") == "Loss"]

    results["total"] = total
    results["wins"] = len(wins)
    results["losses"] = len(losses)
    results["win_rate"] = len(wins) / total * 100 if total > 0 else 0

    # P&L
    gross_pnl = sum(to_float(t.get("gross_pnl")) for t in trades)
    net_pnl = sum(to_float(t.get("net_pnl")) for t in trades)
    total_fees = sum(to_float(t.get("fees")) for t in trades)

    results["gross_pnl"] = gross_pnl
    results["net_pnl"] = net_pnl
    results["total_fees"] = total_fees
    results["fee_drag"] = total_fees / abs(gross_pnl) * 100 if gross_pnl != 0 else 0

    # Exit reasons
    by_reason: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for t in trades:
        reason = t.get("exit_reason", "UNKNOWN")
        by_reason[reason].append(t)

    reason_stats = []
    for reason, tr_list in sorted(by_reason.items()):
        count = len(tr_list)
        w = sum(1 for t in tr_list if t.get("result") == "Win")
        pnl = sum(to_float(t.get("net_pnl")) for t in tr_list)
        avg_hold = sum(to_int(t.get("hold_sec")) for t in tr_list) / count if count > 0 else 0
        reason_stats.append({
            "reason": reason,
            "count": count,
            "wins": w,
            "win_rate": w / count * 100 if count > 0 else 0,
            "net_pnl": pnl,
            "avg_hold_sec": avg_hold,
        })
    results["by_exit_reason"] = reason_stats

    # Side analysis
    by_side: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for t in trades:
        side = t.get("side", "")
        by_side[side].append(t)

    side_stats = []
    for side, tr_list in sorted(by_side.items()):
        count = len(tr_list)
        w = sum(1 for t in tr_list if t.get("result") == "Win")
        pnl = sum(to_float(t.get("net_pnl")) for t in tr_list)
        side_stats.append({
            "side": side,
            "count": count,
            "wins": w,
            "win_rate": w / count * 100 if count > 0 else 0,
            "net_pnl": pnl,
        })
    results["by_side"] = side_stats

    # Hold time analysis
    hold_times = [to_int(t.get("hold_sec")) for t in trades]
    sl_hold_times = [to_int(t.get("hold_sec")) for t in trades if t.get("exit_reason") == "SL"]

    results["avg_hold_sec"] = sum(hold_times) / len(hold_times) if hold_times else 0
    results["min_hold_sec"] = min(hold_times) if hold_times else 0
    results["max_hold_sec"] = max(hold_times) if hold_times else 0

    # Rapid SL hits (< 60 seconds)
    rapid_sl = [t for t in trades if t.get("exit_reason") == "SL" and to_int(t.get("hold_sec")) < 60]
    results["rapid_sl_count"] = len(rapid_sl)
    results["rapid_sl_pnl"] = sum(to_float(t.get("net_pnl")) for t in rapid_sl)
    results["rapid_sl_avg_hold"] = sum(to_int(t.get("hold_sec")) for t in rapid_sl) / len(rapid_sl) if rapid_sl else 0

    # SL distance analysis (how tight were the stops?)
    sl_distances = []
    for t in trades:
        entry = to_float(t.get("entry_price"))
        sl = to_float(t.get("sl"))
        if entry > 0 and sl > 0:
            sl_dist_pct = abs(entry - sl) / entry * 100
            sl_distances.append({
                "trade_id": t.get("trade_id"),
                "entry": entry,
                "sl": sl,
                "sl_dist_pct": sl_dist_pct,
                "result": t.get("result"),
                "exit_reason": t.get("exit_reason"),
            })

    sl_hit_trades = [s for s in sl_distances if s.get("exit_reason") == "SL"]
    if sl_hit_trades:
        results["avg_sl_distance_pct_hit"] = sum(s["sl_dist_pct"] for s in sl_hit_trades) / len(sl_hit_trades)
    else:
        results["avg_sl_distance_pct_hit"] = 0

    # Fee impact - trades that were profitable gross but lost due to fees
    gross_winners_net_losers = [
        t for t in trades
        if to_float(t.get("gross_pnl")) > 0 and to_float(t.get("net_pnl")) < 0
    ]
    results["gross_win_net_loss_count"] = len(gross_winners_net_losers)
    results["gross_win_net_loss_pnl"] = sum(to_float(t.get("net_pnl")) for t in gross_winners_net_losers)

    # Average winning vs losing trade
    avg_win_gross = sum(to_float(t.get("gross_pnl")) for t in wins) / len(wins) if wins else 0
    avg_loss_gross = sum(to_float(t.get("gross_pnl")) for t in losses) / len(losses) if losses else 0

    results["avg_win_gross"] = avg_win_gross
    results["avg_loss_gross"] = avg_loss_gross
    results["profit_factor"] = abs(avg_win_gross / avg_loss_gross) if avg_loss_gross != 0 else 0

    # Risk-reward analysis
    rr_ratios = []
    for t in trades:
        entry = to_float(t.get("entry_price"))
        sl = to_float(t.get("sl"))
        t1 = to_float(t.get("t1"))
        if entry > 0 and sl > 0 and t1 > 0:
            risk = abs(entry - sl)
            reward = abs(t1 - entry)
            if risk > 0:
                rr_ratios.append(reward / risk)

    results["avg_rr_ratio"] = sum(rr_ratios) / len(rr_ratios) if rr_ratios else 0

    # Time of day analysis
    by_hour: Dict[int, List[Dict[str, str]]] = defaultdict(list)
    for t in trades:
        time_str = t.get("entry_time", "00:00:00")
        try:
            hour = int(time_str.split(":")[0])
            by_hour[hour].append(t)
        except Exception:
            pass

    hour_stats = []
    for hour in sorted(by_hour.keys()):
        tr_list = by_hour[hour]
        count = len(tr_list)
        w = sum(1 for t in tr_list if t.get("result") == "Win")
        pnl = sum(to_float(t.get("net_pnl")) for t in tr_list)
        hour_stats.append({
            "hour": hour,
            "count": count,
            "wins": w,
            "win_rate": w / count * 100 if count > 0 else 0,
            "net_pnl": pnl,
        })
    results["by_hour"] = hour_stats

    # Entry price range analysis
    entry_prices = [to_float(t.get("entry_price")) for t in trades]
    if entry_prices:
        results["avg_entry_price"] = sum(entry_prices) / len(entry_prices)
        results["min_entry_price"] = min(entry_prices)
        results["max_entry_price"] = max(entry_prices)

        # Bucket by price range
        price_buckets = defaultdict(list)
        for t in trades:
            price = to_float(t.get("entry_price"))
            if price < 50:
                bucket = "<50"
            elif price < 100:
                bucket = "50-100"
            elif price < 200:
                bucket = "100-200"
            elif price < 500:
                bucket = "200-500"
            else:
                bucket = "500+"
            price_buckets[bucket].append(t)

        price_stats = []
        for bucket in ["<50", "50-100", "100-200", "200-500", "500+"]:
            tr_list = price_buckets.get(bucket, [])
            if tr_list:
                count = len(tr_list)
                w = sum(1 for t in tr_list if t.get("result") == "Win")
                pnl = sum(to_float(t.get("net_pnl")) for t in tr_list)
                price_stats.append({
                    "bucket": bucket,
                    "count": count,
                    "wins": w,
                    "win_rate": w / count * 100 if count > 0 else 0,
                    "net_pnl": pnl,
                })
        results["by_price_range"] = price_stats

    return results


def identify_issues(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Identify key issues and improvement opportunities."""
    issues = []

    # Win rate too low
    if analysis.get("win_rate", 0) < 50:
        issues.append({
            "severity": "HIGH",
            "issue": f"Low win rate: {analysis['win_rate']:.1f}%",
            "impact": "More than half of trades are losses",
            "suggestion": "Tighten entry criteria (higher confidence, stronger signals)"
        })

    # Fees too high
    fee_drag = analysis.get("fee_drag", 0)
    if fee_drag > 50:
        issues.append({
            "severity": "CRITICAL",
            "issue": f"Fee drag: {fee_drag:.1f}% of gross P&L lost to fees",
            "impact": "Fees are destroying profitability",
            "suggestion": "Reduce trade frequency, increase position size per trade, or negotiate lower fees"
        })

    # Rapid SL hits
    rapid_sl = analysis.get("rapid_sl_count", 0)
    total = analysis.get("total", 1)
    if rapid_sl / total > 0.3:
        issues.append({
            "severity": "HIGH",
            "issue": f"Rapid SL hits: {rapid_sl} trades ({rapid_sl/total*100:.1f}%) stopped within 60s",
            "impact": f"Lost {analysis.get('rapid_sl_pnl', 0):.2f} to quick stops",
            "suggestion": "Entry timing is poor or SL too tight. Consider wider SL or better entry confirmation"
        })

    # Gross winners but net losers
    gross_win_net_loss = analysis.get("gross_win_net_loss_count", 0)
    if gross_win_net_loss > 0:
        issues.append({
            "severity": "MEDIUM",
            "issue": f"{gross_win_net_loss} trades were profitable but turned loss due to fees",
            "impact": f"Lost {analysis.get('gross_win_net_loss_pnl', 0):.2f}",
            "suggestion": "Target larger moves to overcome fee drag, or reduce fees"
        })

    # Side-specific issues
    for side_stat in analysis.get("by_side", []):
        if side_stat["win_rate"] < 40 and side_stat["count"] > 3:
            issues.append({
                "severity": "MEDIUM",
                "issue": f"{side_stat['side']} side win rate only {side_stat['win_rate']:.1f}%",
                "impact": f"Net P&L from {side_stat['side']}: {side_stat['net_pnl']:.2f}",
                "suggestion": f"Review {side_stat['side']} entry criteria or avoid {side_stat['side']} in current market"
            })

    # Exit reason issues
    for reason_stat in analysis.get("by_exit_reason", []):
        if reason_stat["reason"] == "SL" and reason_stat["count"] / total > 0.5:
            issues.append({
                "severity": "HIGH",
                "issue": f"SL hit rate: {reason_stat['count']/total*100:.1f}%",
                "impact": "More than half of trades hit stop loss",
                "suggestion": "Consider wider SL, better entry timing, or stronger confirmation"
            })
        if reason_stat["reason"] == "SIDE_FLIP" and reason_stat["count"] > 2:
            issues.append({
                "severity": "MEDIUM",
                "issue": f"Side flip exits: {reason_stat['count']} trades",
                "impact": f"Net P&L from flips: {reason_stat['net_pnl']:.2f}",
                "suggestion": "Market is choppy. Consider waiting for stronger trend confirmation"
            })

    # Risk-reward ratio
    rr = analysis.get("avg_rr_ratio", 0)
    if 0 < rr < 1:
        issues.append({
            "severity": "MEDIUM",
            "issue": f"Risk-reward ratio: {rr:.2f} (risk > reward)",
            "impact": "Risk per trade exceeds expected reward",
            "suggestion": "Adjust SL/T1 levels for better risk-reward (aim for 1.5:1 or better)"
        })

    return issues


def generate_recommendations(analysis: Dict[str, Any], issues: List[Dict[str, Any]]) -> List[str]:
    """Generate actionable recommendations."""
    recs = []

    # Fee management
    if analysis.get("fee_drag", 0) > 30:
        recs.append("FEES: Consider reducing trade count by being more selective (entry_ready + higher confidence threshold)")

    # SL adjustment
    avg_sl_dist = analysis.get("avg_sl_distance_pct_hit", 0)
    if avg_sl_dist > 0:
        recs.append(f"SL: Average SL distance on hit trades: {avg_sl_dist:.1f}%. Consider widening by 2-3%")

    # Entry timing
    rapid_sl = analysis.get("rapid_sl_count", 0)
    if rapid_sl > 3:
        recs.append("ENTRY: Add confirmation delay - wait 15-30 seconds after signal before entering")

    # Time of day
    for h in analysis.get("by_hour", []):
        if h["count"] > 2 and h["win_rate"] < 30:
            recs.append(f"TIMING: Avoid trading at hour {h['hour']}:00 (win rate: {h['win_rate']:.1f}%)")
        if h["count"] > 2 and h["win_rate"] > 70:
            recs.append(f"TIMING: Hour {h['hour']}:00 shows strong results (win rate: {h['win_rate']:.1f}%) - prioritize")

    # Price range
    for p in analysis.get("by_price_range", []):
        if p["count"] > 2 and p["win_rate"] < 30:
            recs.append(f"PREMIUM: Avoid {p['bucket']} price range (win rate: {p['win_rate']:.1f}%)")

    # Risk-reward
    if analysis.get("avg_rr_ratio", 0) < 1:
        recs.append("RR: Adjust T1 target to be at least 1.5x the SL distance for better risk-reward")

    # Exit management
    for reason in analysis.get("by_exit_reason", []):
        if reason["reason"] == "TIME" and reason["net_pnl"] < 0:
            recs.append("EXIT: TIME exits are losing money. Consider tighter time limits or trail stops")

    return recs


def main() -> int:
    p = argparse.ArgumentParser(description="Analyze paper trades for improvement opportunities")
    p.add_argument("--trades-csv", default="paper_trades.csv", help="Paper trades CSV file")
    p.add_argument("--journal-csv", default="decision_journal.csv", help="Decision journal CSV")
    p.add_argument("--output-json", default="", help="Optional: output analysis to JSON file")
    args = p.parse_args()

    trades = load_csv(args.trades_csv)

    if not trades:
        print(f"No trades found in {args.trades_csv}")
        return 1

    print_section("PAPER TRADE DEEP ANALYSIS")
    print(f"  File: {args.trades_csv}")
    print(f"  Trades: {len(trades)}")

    analysis = analyze_trades(trades)

    # Summary
    print_section("SUMMARY")
    summary_rows = [
        ["Total Trades", str(analysis["total"])],
        ["Wins", str(analysis["wins"])],
        ["Losses", str(analysis["losses"])],
        ["Win Rate", f"{analysis['win_rate']:.1f}%"],
        ["Gross P&L", f"{analysis['gross_pnl']:.2f}"],
        ["Net P&L", f"{analysis['net_pnl']:.2f}"],
        ["Total Fees", f"{analysis['total_fees']:.2f}"],
        ["Fee Drag", f"{analysis['fee_drag']:.1f}%"],
        ["Avg Win (Gross)", f"{analysis['avg_win_gross']:.2f}"],
        ["Avg Loss (Gross)", f"{analysis['avg_loss_gross']:.2f}"],
        ["Profit Factor", f"{analysis['profit_factor']:.2f}"],
        ["Avg R:R Ratio", f"{analysis['avg_rr_ratio']:.2f}"],
    ]
    print_table(["Metric", "Value"], summary_rows)

    # Hold time
    print_section("HOLD TIME ANALYSIS")
    hold_rows = [
        ["Avg Hold (sec)", f"{analysis['avg_hold_sec']:.1f}"],
        ["Min Hold (sec)", str(analysis["min_hold_sec"])],
        ["Max Hold (sec)", str(analysis["max_hold_sec"])],
        ["Rapid SL (<60s)", str(analysis["rapid_sl_count"])],
        ["Rapid SL P&L", f"{analysis['rapid_sl_pnl']:.2f}"],
        ["Rapid SL Avg Hold", f"{analysis['rapid_sl_avg_hold']:.1f}s"],
    ]
    print_table(["Metric", "Value"], hold_rows)

    # By exit reason
    print_section("EXIT REASON ANALYSIS")
    reason_rows = []
    for r in analysis.get("by_exit_reason", []):
        reason_rows.append([
            r["reason"],
            str(r["count"]),
            str(r["wins"]),
            f"{r['win_rate']:.1f}%",
            f"{r['net_pnl']:.2f}",
            f"{r['avg_hold_sec']:.0f}s",
        ])
    print_table(["Reason", "Count", "Wins", "WinRate", "NetP&L", "AvgHold"], reason_rows)

    # By side
    print_section("SIDE ANALYSIS")
    side_rows = []
    for s in analysis.get("by_side", []):
        side_rows.append([
            s["side"],
            str(s["count"]),
            str(s["wins"]),
            f"{s['win_rate']:.1f}%",
            f"{s['net_pnl']:.2f}",
        ])
    print_table(["Side", "Count", "Wins", "WinRate", "NetP&L"], side_rows)

    # By hour
    print_section("TIME OF DAY ANALYSIS")
    hour_rows = []
    for h in analysis.get("by_hour", []):
        hour_rows.append([
            f"{h['hour']:02d}:00",
            str(h["count"]),
            str(h["wins"]),
            f"{h['win_rate']:.1f}%",
            f"{h['net_pnl']:.2f}",
        ])
    print_table(["Hour", "Count", "Wins", "WinRate", "NetP&L"], hour_rows)

    # By price range
    if analysis.get("by_price_range"):
        print_section("ENTRY PRICE ANALYSIS")
        price_rows = []
        for pr in analysis.get("by_price_range", []):
            price_rows.append([
                pr["bucket"],
                str(pr["count"]),
                str(pr["wins"]),
                f"{pr['win_rate']:.1f}%",
                f"{pr['net_pnl']:.2f}",
            ])
        print_table(["Price Range", "Count", "Wins", "WinRate", "NetP&L"], price_rows)

    # Issues
    issues = identify_issues(analysis)
    print_section("IDENTIFIED ISSUES")
    if issues:
        for i, issue in enumerate(issues, 1):
            print(f"\n  [{issue['severity']}] Issue #{i}: {issue['issue']}")
            print(f"    Impact: {issue['impact']}")
            print(f"    Suggestion: {issue['suggestion']}")
    else:
        print("  No major issues identified")

    # Recommendations
    recs = generate_recommendations(analysis, issues)
    print_section("ACTIONABLE RECOMMENDATIONS")
    if recs:
        for i, rec in enumerate(recs, 1):
            print(f"  {i}. {rec}")
    else:
        print("  No specific recommendations at this time")

    # Save to JSON if requested
    if args.output_json:
        output = {
            "analysis": analysis,
            "issues": issues,
            "recommendations": recs,
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        print(f"\n  Analysis saved to: {args.output_json}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
