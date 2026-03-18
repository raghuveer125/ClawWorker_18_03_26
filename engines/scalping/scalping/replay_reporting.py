"""
Replay diagnostics and reporting helpers shared by API and CLI replay runners.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def flatten_signals(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        flattened: List[Dict[str, Any]] = []
        for symbol, selections in payload.items():
            if isinstance(selections, list):
                for selection in selections:
                    if isinstance(selection, dict):
                        flattened.append({"symbol": symbol, **selection})
                    elif hasattr(selection, "__dict__"):
                        flattened.append({"symbol": symbol, **selection.__dict__})
            elif isinstance(selections, dict):
                flattened.append({"symbol": symbol, **selections})
        return flattened
    return []


def average(values: List[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _rr_bucket(rr_ratio: float) -> str:
    if rr_ratio >= 1.5:
        return "1.5+"
    if rr_ratio >= 1.0:
        return "1.2"
    return "0.8"


def _trade_expectancy(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [trade for trade in trades if trade.get("status") == "closed"]
    pnl_values = [safe_float(trade.get("realized_pnl")) for trade in closed]
    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl < 0]
    total = len(closed)
    return {
        "trades": total,
        "win_rate": round((len(wins) / total) * 100, 2) if total else 0.0,
        "expectancy": round(sum(pnl_values) / total, 2) if total else 0.0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "net_pnl": round(sum(pnl_values), 2),
    }


def _extract_trade_features(trade: Dict[str, Any]) -> Dict[str, Any]:
    packet = dict(trade.get("decision_packet", {}) or {})
    timeframe_alignment = dict(packet.get("timeframe_alignment", {}) or {})
    micro_momentum = dict(packet.get("micro_momentum", {}) or {})
    entry_trigger = dict(packet.get("entry_trigger", {}) or {})
    rr_ratio = safe_float(packet.get("rr_ratio"))
    tag = str(packet.get("setup_tag", packet.get("quality_grade", "unknown")) or "unknown")
    three_tf_aligned = bool(timeframe_alignment.get("three_tf_aligned"))
    micro_present = bool(micro_momentum.get("aligned")) or safe_float(micro_momentum.get("score")) > 0
    trigger_active = bool(entry_trigger.get("active"))
    return {
        "trade_id": trade.get("trade_id"),
        "symbol": trade.get("symbol"),
        "strike": trade.get("strike"),
        "option_type": trade.get("option_type"),
        "tag": tag,
        "rr_ratio": round(rr_ratio, 4),
        "rr_bucket": _rr_bucket(rr_ratio),
        "timeframe_alignment": {
            "1m": timeframe_alignment.get("1m_trend", "neutral"),
            "1m_aligned": bool(timeframe_alignment.get("1m_aligned")),
            "3m_breakout": timeframe_alignment.get("3m_breakout"),
            "3m_breakout_aligned": bool(timeframe_alignment.get("3m_breakout_aligned")),
            "5m": timeframe_alignment.get("5m_trend", "neutral"),
            "5m_aligned": bool(timeframe_alignment.get("5m_aligned")),
            "three_tf_aligned": three_tf_aligned,
        },
        "micro_momentum": {
            "score": safe_float(micro_momentum.get("score")),
            "timing": micro_momentum.get("timing", "unknown"),
            "present": micro_present,
        },
        "entry_trigger": {
            "active": trigger_active,
            "types": list(entry_trigger.get("types", []) or []),
        },
        "outcome": trade.get("outcome", "unknown"),
        "realized_pnl": round(safe_float(trade.get("realized_pnl")), 2),
    }


def _build_strategy_quality_summary(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        if trade.get("status") != "closed":
            continue
        features = _extract_trade_features(trade)
        grouped[str(features.get("tag", "unknown"))].append(trade)

    summary: Dict[str, Any] = {}
    for tag, grouped_trades in sorted(grouped.items()):
        stats = _trade_expectancy(grouped_trades)
        rr_values = [safe_float((trade.get("decision_packet", {}) or {}).get("rr_ratio")) for trade in grouped_trades]
        summary[tag] = {
            **stats,
            "average_rr": average([value for value in rr_values if value > 0]),
        }
    return summary


def _build_edge_discovery(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [trade for trade in trades if trade.get("status") == "closed"]
    feature_log = [_extract_trade_features(trade) for trade in closed]
    three_tf_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    micro_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    rr_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    combo_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for trade, features in zip(closed, feature_log):
        three_tf_groups["aligned" if features["timeframe_alignment"]["three_tf_aligned"] else "not_aligned"].append(trade)
        micro_groups["present" if features["micro_momentum"]["present"] else "absent"].append(trade)
        rr_groups[features["rr_bucket"]].append(trade)
        combo_key = (
            f"3tf={'yes' if features['timeframe_alignment']['three_tf_aligned'] else 'no'}|"
            f"micro={'yes' if features['micro_momentum']['present'] else 'no'}|"
            f"rr={features['rr_bucket']}|"
            f"trigger={'yes' if features['entry_trigger']['active'] else 'no'}"
        )
        combo_groups[combo_key].append(trade)

    combo_expectancy = {
        key: _trade_expectancy(group)
        for key, group in sorted(combo_groups.items(), key=lambda item: _trade_expectancy(item[1])["expectancy"], reverse=True)
    }

    return {
        "trade_feature_log": feature_log,
        "three_tf_alignment": {key: _trade_expectancy(group) for key, group in sorted(three_tf_groups.items())},
        "micro_momentum": {key: _trade_expectancy(group) for key, group in sorted(micro_groups.items())},
        "rr_buckets": {key: _trade_expectancy(group) for key, group in sorted(rr_groups.items())},
        "combo_expectancy": combo_expectancy,
    }


class ReplayDiagnosticsTracker:
    def __init__(self) -> None:
        self.stage_totals = {
            "total_strike_selections": 0,
            "total_quality_pass": 0,
            "total_liquidity_pass": 0,
            "total_trades": 0,
        }
        self.rejection_breakdown: Counter[str] = Counter()
        self.confidence_values: List[float] = []
        self.spread_values: List[float] = []
        self.volume_values: List[float] = []
        self.oi_values: List[float] = []
        self.heatmap = {
            "StructureAgent": 0,
            "MomentumAgent": 0,
            "StrikeSelector": 0,
            "QualityFilter": 0,
            "LiquidityFilter": 0,
            "Execution": 0,
        }

    def observe_cycle(self, context: Any, results: Dict[str, Any]) -> None:
        strike_signals = flatten_signals(getattr(context, "data", {}).get("strike_selections", {}))
        quality_signals = flatten_signals(getattr(context, "data", {}).get("quality_filtered_signals", []))
        liquidity_signals = flatten_signals(getattr(context, "data", {}).get("liquidity_filtered_selections", []))
        rejected_signals = flatten_signals(getattr(context, "data", {}).get("rejected_signals", []))
        executed_trades = list(getattr(context, "data", {}).get("executed_trades", []))

        self.stage_totals["total_strike_selections"] += len(strike_signals)
        self.stage_totals["total_quality_pass"] += len(quality_signals)
        self.stage_totals["total_liquidity_pass"] += len(liquidity_signals)
        self.stage_totals["total_trades"] = len(executed_trades)

        if "structure" in results:
            self.heatmap["StructureAgent"] += int(results["structure"].metrics.get("breaks_detected", 0))
        if "momentum" in results:
            self.heatmap["MomentumAgent"] += int(results["momentum"].metrics.get("total_signals", 0))
        if "strike_selector" in results:
            self.heatmap["StrikeSelector"] += int(results["strike_selector"].output.get("total_selections", 0))
        if "signal_quality" in results:
            self.heatmap["QualityFilter"] += int(results["signal_quality"].output.get("signals_passed", 0))
        self.heatmap["LiquidityFilter"] += len(liquidity_signals)
        self.heatmap["Execution"] = len(executed_trades)

        for signal in strike_signals:
            confidence = safe_float(signal.get("confidence", signal.get("score", signal.get("quality_score"))))
            if confidence:
                self.confidence_values.append(confidence if confidence <= 1.0 else confidence / 100.0)
            spread_pct = safe_float(signal.get("spread_pct"))
            if spread_pct:
                self.spread_values.append(spread_pct)
            volume = safe_float(signal.get("volume"))
            if volume:
                self.volume_values.append(volume)
            oi = safe_float(signal.get("oi", signal.get("open_interest")))
            if oi:
                self.oi_values.append(oi)

        for signal in rejected_signals:
            for reason in signal.get("rejection_reasons", []) or ["unknown"]:
                self.rejection_breakdown[str(reason)] += 1

    def build_report(self, trades: List[Dict[str, Any]], simulated_pnl: float) -> Dict[str, Any]:
        closed_trades = [trade for trade in trades if trade.get("status") == "closed"]
        winning_trades = [trade for trade in closed_trades if safe_float(trade.get("realized_pnl")) > 0]
        losing_trades = [trade for trade in closed_trades if safe_float(trade.get("realized_pnl")) < 0]
        gross_profit = sum(safe_float(trade.get("realized_pnl")) for trade in winning_trades)
        gross_loss = abs(sum(safe_float(trade.get("realized_pnl")) for trade in losing_trades))

        report = {
            "stage_totals": dict(self.stage_totals),
            "rejection_breakdown": dict(sorted(self.rejection_breakdown.items(), key=lambda item: (-item[1], item[0]))),
            "signal_stats": {
                "average_confidence": average(self.confidence_values),
                "average_spread_pct": average(self.spread_values),
                "average_volume": average(self.volume_values),
                "average_open_interest": average(self.oi_values),
            },
            "pipeline_heatmap": dict(self.heatmap),
            "trades_executed": len(trades),
            "simulated_pnl": simulated_pnl,
            "win_rate": round(len(winning_trades) / len(closed_trades) * 100, 2) if closed_trades else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
            "strategy_quality": _build_strategy_quality_summary(trades),
            "edge_discovery": _build_edge_discovery(trades),
        }
        report.update(self._build_pipeline_summary(report))
        return report

    def _build_pipeline_summary(self, report: Dict[str, Any]) -> Dict[str, Any]:
        stage_totals = report["stage_totals"]
        rejection_breakdown = report["rejection_breakdown"]

        stage_losses = {
            "strike_selections": stage_totals["total_strike_selections"] - stage_totals["total_quality_pass"],
            "quality_filtered_signals": stage_totals["total_quality_pass"] - stage_totals["total_liquidity_pass"],
            "liquidity_filtered_selections": stage_totals["total_liquidity_pass"] - stage_totals["total_trades"],
        }
        largest_drop = max(stage_losses.items(), key=lambda item: item[1]) if stage_losses else ("none", 0)
        top_reason = max(rejection_breakdown.items(), key=lambda item: item[1]) if rejection_breakdown else ("none", 0)
        blocked_stage = "Execution" if stage_totals["total_liquidity_pass"] > 0 and stage_totals["total_trades"] == 0 else largest_drop[0]

        return {
            "largest_pipeline_drop": {"stage": largest_drop[0], "count": largest_drop[1]},
            "top_rejection_reason": {"reason": top_reason[0], "count": top_reason[1]},
            "signals_blocked_stage": blocked_stage,
            "diagnostic_report": (
                "DIAGNOSTIC REPORT\n"
                "stage_totals:\n"
                f"  total_strike_selections={stage_totals['total_strike_selections']}\n"
                f"  total_quality_pass={stage_totals['total_quality_pass']}\n"
                f"  total_liquidity_pass={stage_totals['total_liquidity_pass']}\n"
                f"  total_trades={stage_totals['total_trades']}\n"
                "rejection_breakdown:\n"
                + (
                    "\n".join(
                        f"  {reason}={count}" for reason, count in sorted(rejection_breakdown.items(), key=lambda item: (-item[1], item[0]))
                    )
                    if rejection_breakdown
                    else "  none=0"
                )
                + "\n"
                "signal_stats:\n"
                f"  average_confidence={report['signal_stats']['average_confidence']}\n"
                f"  average_spread_pct={report['signal_stats']['average_spread_pct']}\n"
                f"  average_volume={report['signal_stats']['average_volume']}\n"
                f"  average_open_interest={report['signal_stats']['average_open_interest']}\n"
                "pipeline_dropoff:\n"
                f"  strike_to_quality={stage_losses['strike_selections']}\n"
                f"  quality_to_liquidity={stage_losses['quality_filtered_signals']}\n"
                f"  liquidity_to_trade={stage_losses['liquidity_filtered_selections']}\n"
                f"  biggest_drop={largest_drop[0]}:{largest_drop[1]}\n"
                f"  top_rejection_reason={top_reason[0]}:{top_reason[1]}"
            ),
        }
