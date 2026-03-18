#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import os
from typing import Any, Dict, List, Tuple


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


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
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def print_table(headers: List[str], rows: List[List[str]]) -> None:
    if not rows:
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def fmt(row: List[str]) -> str:
        return " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))

    sep = "-+-".join("-" * w for w in widths)
    print(fmt(headers))
    print(sep)
    for row in rows:
        print(fmt(row))


def summarize(trades: List[Dict[str, str]], state: Dict[str, Any]) -> Dict[str, float]:
    closed = len(trades)
    wins = 0
    losses = 0
    gross = 0.0
    net = 0.0
    fees = 0.0

    for r in trades:
        gross += to_float(r.get("gross_pnl", "0"))
        net += to_float(r.get("net_pnl", "0"))
        fees += to_float(r.get("fees", "0"))
        result = (r.get("result", "") or "").strip().lower()
        if result == "win":
            wins += 1
        elif result == "loss":
            losses += 1

    open_positions = state.get("open_positions", []) or []
    open_count = len(open_positions)
    cash = to_float(state.get("cash", 0.0))
    unrealized = 0.0
    inventory_value = 0.0
    for p in open_positions:
        qty = int(to_float(p.get("qty", 0), 0))
        entry = to_float(p.get("entry_price", 0.0))
        last = to_float(p.get("last_price", entry))
        inventory_value += last * qty
        unrealized += (last - entry) * qty
    equity = cash + inventory_value
    win_rate = (wins / closed * 100.0) if closed else 0.0

    return {
        "closed": float(closed),
        "open": float(open_count),
        "total": float(closed + open_count),
        "wins": float(wins),
        "losses": float(losses),
        "win_rate": win_rate,
        "gross": gross,
        "net": net,
        "fees": fees,
        "cash": cash,
        "unrealized": unrealized,
        "equity": equity,
    }


def best_worst(trades: List[Dict[str, str]]) -> Tuple[Dict[str, str], Dict[str, str]]:
    if not trades:
        return {}, {}
    sorted_rows = sorted(trades, key=lambda r: to_float(r.get("net_pnl", "0")))
    return sorted_rows[-1], sorted_rows[0]


def main() -> int:
    p = argparse.ArgumentParser(description="Show paper trading report.")
    p.add_argument("--trades-csv", default="paper_trades.csv")
    p.add_argument("--equity-csv", default="paper_equity.csv")
    p.add_argument("--state-file", default=".paper_trade_state.json")
    p.add_argument("--last", type=int, default=10, help="Show last N closed trades")
    args = p.parse_args()

    trades = load_csv(args.trades_csv)
    state = load_json(args.state_file)
    equity_rows = load_csv(args.equity_csv)

    s = summarize(trades, state)
    best, worst = best_worst(trades)

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Paper Report | {now}")
    print()

    summary_rows = [[
        str(int(s["closed"])),
        str(int(s["open"])),
        str(int(s["total"])),
        str(int(s["wins"])),
        str(int(s["losses"])),
        f'{s["win_rate"]:.2f}%',
    ]]
    print("Trades")
    print_table(["Closed", "Open", "Total", "Wins", "Losses", "WinRate"], summary_rows)
    print()

    capital_rows = [[
        f'{s["cash"]:.2f}',
        f'{s["unrealized"]:.2f}',
        f'{s["equity"]:.2f}',
        f'{s["gross"]:.2f}',
        f'{s["net"]:.2f}',
        f'{s["fees"]:.2f}',
    ]]
    print("Capital")
    print_table(["Cash", "Unrealized", "Equity", "GrossPnL", "NetPnL", "Fees"], capital_rows)
    print()

    if best and worst:
        extreme_rows = [
            [
                "Best",
                str(best.get("trade_id", "")),
                str(best.get("side", "")),
                str(best.get("strike", "")),
                str(best.get("entry_price", "")),
                str(best.get("exit_price", "")),
                str(best.get("exit_reason", "")),
                str(best.get("net_pnl", "")),
            ],
            [
                "Worst",
                str(worst.get("trade_id", "")),
                str(worst.get("side", "")),
                str(worst.get("strike", "")),
                str(worst.get("entry_price", "")),
                str(worst.get("exit_price", "")),
                str(worst.get("exit_reason", "")),
                str(worst.get("net_pnl", "")),
            ],
        ]
        print("Extremes")
        print_table(["Type", "ID", "Side", "Strike", "Entry", "Exit", "Reason", "NetPnL"], extreme_rows)
        print()

    if trades:
        print(f"Last {max(1, args.last)} Closed Trades")
        rows: List[List[str]] = []
        for r in trades[-max(1, args.last):]:
            rows.append(
                [
                    str(r.get("trade_id", "")),
                    str(r.get("side", "")),
                    str(r.get("strike", "")),
                    f'{r.get("entry_date", "")} {r.get("entry_time", "")}',
                    str(r.get("entry_price", "")),
                    f'{r.get("exit_date", "")} {r.get("exit_time", "")}',
                    str(r.get("exit_price", "")),
                    str(r.get("exit_reason", "")),
                    str(r.get("fees", "")),
                    str(r.get("net_pnl", "")),
                    str(r.get("result", "")),
                ]
            )
        print_table(
            ["ID", "Side", "Strike", "EntryAt", "Entry", "ExitAt", "Exit", "Reason", "Fees", "NetPnL", "Result"],
            rows,
        )
        print()

    open_positions = state.get("open_positions", []) or []
    if open_positions:
        print("Open Positions")
        rows = []
        for p in open_positions:
            qty = int(to_float(p.get("qty", 0), 0))
            entry = to_float(p.get("entry_price", 0.0))
            last = to_float(p.get("last_price", entry))
            u = (last - entry) * qty
            rows.append(
                [
                    str(p.get("trade_id", "")),
                    str(p.get("side", "")),
                    str(p.get("strike", "")),
                    str(qty),
                    f"{entry:.2f}",
                    f"{last:.2f}",
                    f"{to_float(p.get('sl', 0.0)):.2f}",
                    f"{to_float(p.get('t1', 0.0)):.2f}",
                    f"{u:.2f}",
                ]
            )
        print_table(["ID", "Side", "Strike", "Qty", "Entry", "LTP", "SL", "T1", "U-PnL"], rows)
        print()

    if equity_rows:
        last_eq = equity_rows[-1]
        print(
            "Last Equity Snapshot: "
            f"{last_eq.get('date', '')} {last_eq.get('time', '')} | "
            f"cash={last_eq.get('cash', '')} equity={last_eq.get('equity', '')} "
            f"realized={last_eq.get('realized_pnl', '')} unrealized={last_eq.get('unrealized_pnl', '')}"
        )
        print()

    print("Files")
    print(f"- trades: {args.trades_csv}")
    print(f"- equity: {args.equity_csv}")
    print(f"- state : {args.state_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
