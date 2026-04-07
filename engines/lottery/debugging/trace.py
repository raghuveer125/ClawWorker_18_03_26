"""Stepwise debug trace — per-cycle snapshot of all decisions and rejections.

Captures every step of the pipeline for a single cycle:
1. Fetch summary
2. Validation checks
3. Derived variables
4. Side bias decision
5. Strike scan results
6. Final selection
7. Trade/no-trade decision
8. Paper execution result

Each trace is a DebugTrace dataclass that can be persisted to SQLite
and served via API for debugging.

Failure buckets categorize errors for dashboarding.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from ..config import LotteryConfig
from ..models import (
    ChainSnapshot,
    CalculatedRow,
    DebugTrace,
    PaperTrade,
    QualityReport,
    SignalEvent,
    Side,
)
from ..calculations.scoring import ScoredCandidate


class CycleTracer:
    """Builds a DebugTrace for one calculation cycle.

    Usage:
        tracer = CycleTracer(config, symbol)
        tracer.record_fetch(snapshot)
        tracer.record_validation(report)
        ...
        trace = tracer.build()
    """

    def __init__(self, config: LotteryConfig, symbol: str) -> None:
        self._config = config
        self._symbol = symbol
        self._start_time = time.monotonic()
        self._snapshot_id = ""
        self._steps: dict = {}
        self._latencies: dict[str, float] = {}

    def record_fetch(self, snapshot: Optional[ChainSnapshot], elapsed_ms: float) -> None:
        """Record fetch step results."""
        self._latencies["fetch_ms"] = round(elapsed_ms, 2)
        if snapshot:
            self._snapshot_id = snapshot.snapshot_id
            self._steps["fetch_summary"] = {
                "success": True,
                "spot_ltp": snapshot.spot_ltp,
                "rows": len(snapshot.rows),
                "strikes": len(snapshot.strikes),
                "expiry": snapshot.expiry,
            }
        else:
            self._steps["fetch_summary"] = {
                "success": False,
                "error": "chain fetch returned None",
            }

    def record_validation(self, report: QualityReport, elapsed_ms: float) -> None:
        """Record validation step results."""
        self._latencies["validation_ms"] = round(elapsed_ms, 2)
        self._steps["validation_result"] = {
            "overall": report.overall_status.value,
            "score": report.quality_score,
            "checks": {
                c.check_name: c.status.value for c in report.checks
            },
            "failed": [
                c.check_name for c in report.checks
                if c.status.value == "FAIL"
            ],
        }

    def record_calculations(
        self,
        rows_count: int,
        window_count: int,
        band_ce_count: int,
        band_pe_count: int,
        elapsed_ms: float,
    ) -> None:
        """Record calculation step summary."""
        self._latencies["calculation_ms"] = round(elapsed_ms, 2)
        self._steps["derived_variables"] = {
            "total_strikes": rows_count,
            "window_strikes": window_count,
            "band_eligible_ce": band_ce_count,
            "band_eligible_pe": band_pe_count,
        }

    def record_side_bias(
        self,
        preferred_side: Optional[Side],
        bias_score: Optional[float],
        avg_call_decay: Optional[float],
        avg_put_decay: Optional[float],
    ) -> None:
        """Record side bias decision."""
        self._steps["side_bias_decision"] = {
            "preferred_side": preferred_side.value if preferred_side else None,
            "bias_score": bias_score,
            "avg_call_decay": avg_call_decay,
            "avg_put_decay": avg_put_decay,
        }

    def record_strike_scan(
        self,
        total_candidates: int,
        ce_candidates: int,
        pe_candidates: int,
        extrapolated_ce: int,
        extrapolated_pe: int,
    ) -> None:
        """Record strike scanning results."""
        self._steps["strike_scan_results"] = {
            "total_candidates": total_candidates,
            "ce_candidates": ce_candidates,
            "pe_candidates": pe_candidates,
            "extrapolated_ce": extrapolated_ce,
            "extrapolated_pe": extrapolated_pe,
        }

    def record_selection(
        self,
        best_ce: Optional[ScoredCandidate],
        best_pe: Optional[ScoredCandidate],
        elapsed_ms: float,
    ) -> None:
        """Record final selection."""
        self._latencies["scoring_ms"] = round(elapsed_ms, 2)
        self._steps["final_selection"] = {
            "best_ce": {
                "strike": best_ce.strike,
                "ltp": best_ce.ltp,
                "score": best_ce.score,
                "source": best_ce.source,
            } if best_ce else None,
            "best_pe": {
                "strike": best_pe.strike,
                "ltp": best_pe.ltp,
                "score": best_pe.score,
                "source": best_pe.source,
            } if best_pe else None,
        }

    def record_trade_decision(self, signal: SignalEvent) -> None:
        """Record the trade/no-trade decision."""
        self._steps["trade_decision"] = {
            "validity": signal.validity.value,
            "machine_state": signal.machine_state.value,
            "zone": signal.zone,
            "selected_strike": signal.selected_strike,
            "selected_premium": signal.selected_premium,
            "rejection_reason": signal.rejection_reason.value if signal.rejection_reason else None,
            "rejection_detail": signal.rejection_detail,
        }

    def record_paper_execution(
        self,
        trade: Optional[PaperTrade],
        action: str,
    ) -> None:
        """Record paper execution result."""
        if trade:
            self._steps["paper_execution"] = {
                "action": action,
                "trade_id": trade.trade_id,
                "strike": trade.strike,
                "side": trade.side.value,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "pnl": trade.pnl,
                "status": trade.status.value,
            }
        else:
            self._steps["paper_execution"] = {
                "action": action,
                "trade_id": None,
            }

    def build(self) -> DebugTrace:
        """Build the final DebugTrace for this cycle."""
        total_ms = (time.monotonic() - self._start_time) * 1000
        self._latencies["total_ms"] = round(total_ms, 2)

        return DebugTrace(
            symbol=self._symbol,
            snapshot_id=self._snapshot_id,
            config_version=self._config.version_hash,
            fetch_summary=self._steps.get("fetch_summary"),
            validation_result=self._steps.get("validation_result"),
            derived_variables=self._steps.get("derived_variables"),
            side_bias_decision=self._steps.get("side_bias_decision"),
            strike_scan_results=self._steps.get("strike_scan_results"),
            final_selection=self._steps.get("final_selection"),
            trade_decision=self._steps.get("trade_decision"),
            paper_execution=self._steps.get("paper_execution"),
            latency_ms=self._latencies,
        )


# ── Failure Buckets ────────────────────────────────────────────────────────

class FailureBucket:
    """Categorize errors into buckets for dashboarding."""

    BUCKETS = (
        "DATA_FETCH",
        "PARSING",
        "VALIDATION",
        "STALE_DATA",
        "MISSING_STRIKE",
        "STRATEGY_REJECTION",
        "PAPER_TRADING",
        "PERSISTENCE",
    )

    def __init__(self) -> None:
        self._counts: dict[str, int] = {b: 0 for b in self.BUCKETS}
        self._recent: dict[str, list[str]] = {b: [] for b in self.BUCKETS}
        self._max_recent = 10

    def record(self, bucket: str, detail: str) -> None:
        """Record a failure in a bucket."""
        if bucket not in self._counts:
            bucket = "PARSING"  # fallback
        self._counts[bucket] += 1
        recent = self._recent[bucket]
        recent.append(f"{datetime.now(timezone.utc).isoformat()}: {detail}")
        if len(recent) > self._max_recent:
            self._recent[bucket] = recent[-self._max_recent:]

    def get_summary(self) -> dict:
        """Get failure summary for dashboard."""
        return {
            "counts": dict(self._counts),
            "total": sum(self._counts.values()),
            "recent": {
                b: self._recent[b][-3:]
                for b in self.BUCKETS
                if self._recent[b]
            },
        }

    def reset(self) -> None:
        """Reset all counters."""
        self._counts = {b: 0 for b in self.BUCKETS}
        self._recent = {b: [] for b in self.BUCKETS}
