"""Session report generator — produces SESSION_REPORT.md."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List


def generate_session_report(
    session_dir: str,
    meta: Dict[str, Any],
    transitions: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    cycle_count: int,
    active_cycles: int,
    tradable_cycles: int,
) -> None:
    """Generate SESSION_REPORT.md from session data."""
    path = os.path.join(session_dir, "SESSION_REPORT.md")

    activations = [t for t in transitions if t.get("to_state") == "ACTIVE"]
    wins = [t for t in trades if t.get("result") == "WIN"]
    losses = [t for t in trades if t.get("result") == "LOSS"]
    tradable_pct = (tradable_cycles / active_cycles * 100) if active_cycles > 0 else 0

    lines = [
        f"# EOE Shadow Session Report — {meta.get('session_date', 'unknown')}",
        "",
        "## 1. Session Overview",
        f"- Date: {meta.get('session_date')}",
        f"- Expiry: {meta.get('expiry_type')}",
        f"- Index: {meta.get('index', 'BSE:SENSEX-INDEX')}",
        f"- Classification: {meta.get('session_classification', 'unknown')}",
        f"- Open: ₹{meta.get('open', 0):,.2f} | High: ₹{meta.get('high', 0):,.2f} | Low: ₹{meta.get('low', 0):,.2f} | Close: ₹{meta.get('close', 0):,.2f}",
        f"- Gap: {meta.get('gap_pct', 0):.2f}% | Range: {meta.get('day_range_pct', 0):.2f}% | Reversal: {meta.get('reversal_magnitude_pct', 0):.2f}%",
        "",
        "## 2. EOE Activations",
        f"- Total state transitions: {len(transitions)}",
        f"- ACTIVE reached: {len(activations)} times",
        f"- Total cycles: {cycle_count}",
        "",
        "## 3. Candidate Trades",
        f"- Trades entered: {len(trades)}",
        f"- Wins: {len(wins)}, Losses: {len(losses)}",
        "",
        "## 4. Tradability Assessment",
        f"- ACTIVE cycles with tradable premium: {tradable_cycles}/{active_cycles} ({tradable_pct:.0f}%)",
        f"- Verdict: {'PASS' if tradable_pct >= 60 else 'MARGINAL' if tradable_pct >= 40 else 'FAIL'}",
        "",
        "## 5. Outcome Summary",
        "",
        "| Strike | Entry | Peak | Exit | Payoff | MFE | MAE | Hold | Result |",
        "|--------|-------|------|------|--------|-----|-----|------|--------|",
    ]

    for t in trades:
        lines.append(
            f"| {t.get('strike', '')} | ₹{t.get('entry_premium', 0):.1f} | ₹{t.get('peak_premium', 0):.1f} "
            f"| ₹{t.get('exit_premium', 0):.1f} | {t.get('payoff_multiple', 0):.1f}x "
            f"| {t.get('mfe_multiple', 0):.1f}x | {t.get('mae_multiple', 0):.1f}x "
            f"| {t.get('hold_time_min', 0):.0f}m | {t.get('result', '')} |"
        )

    lines += [
        "",
        "## 6. Gate-Relevant Metrics",
        f"- G2 activations: {len(activations)}",
        f"- G6 tradable rate: {tradable_pct:.0f}%",
        f"- G7 max loss: ₹{max((abs(t.get('exit_premium',0) - t.get('entry_premium',0)) * 10 for t in losses), default=0):.0f}",
        f"- G8 MFE >3x rate: {sum(1 for t in trades if t.get('mfe_multiple', 0) >= 3) / max(len(trades), 1) * 100:.0f}%",
        "",
        "## 7. Failure Modes",
        f"- F2 spread trap: {sum(1 for t in trades if t.get('spread_trap'))} / {len(trades)}",
        "",
        "## 8. Session Verdict",
        f"- Market provided opportunity: {'YES' if meta.get('reversal_occurred') else 'NO'}",
        f"- EOE activated correctly: {'YES' if len(activations) > 0 else 'NO' if meta.get('reversal_occurred') else 'N/A'}",
        f"- Trade quality: {'GOOD' if wins else 'POOR' if losses else 'NO_TRADE'}",
    ]

    try:
        with open(path, "w") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
