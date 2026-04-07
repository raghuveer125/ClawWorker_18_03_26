"""Snapshot validation engine — 9 quality checks producing PASS/WARN/FAIL.

Runs all checks before any calculation is performed.
Each check is independent and produces a QualityCheck result.
The composite QualityReport determines whether to proceed, warn, or reject.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from ..config import LotteryConfig, SnapshotMode
from ..models import (
    ChainSnapshot,
    OptionRow,
    OptionType,
    QualityCheck,
    QualityReport,
    QualityStatus,
)

logger = logging.getLogger(__name__)


class DataQualityValidator:
    """Pre-calculation data quality engine with 9 check categories.

    Stateful: tracks previous snapshot hashes to detect staleness.
    Symbol-agnostic: works for any instrument.
    """

    def __init__(self, config: LotteryConfig) -> None:
        self._cfg = config.data_quality
        self._snapshot_mode = config.data_quality.snapshot_mode
        self._strike_step = config.instrument.strike_step
        self._window_size = config.window.size
        self._prev_hashes: list[str] = []
        self._max_stale = self._cfg.max_stale_cycles

    def validate(
        self,
        snapshot: ChainSnapshot,
        prev_snapshot: Optional[ChainSnapshot] = None,
    ) -> QualityReport:
        """Run all 9 quality checks on a chain snapshot.

        Args:
            snapshot: Current option chain snapshot.
            prev_snapshot: Previous snapshot for staleness comparison.

        Returns:
            QualityReport with overall status and individual check results.
        """
        checks: list[QualityCheck] = []

        checks.append(self._check_timestamp_freshness(snapshot))
        checks.append(self._check_null_missing(snapshot))
        checks.append(self._check_duplicate_stale(snapshot, prev_snapshot))
        checks.extend(self._check_price_sanity(snapshot))
        checks.append(self._check_strike_continuity(snapshot))
        checks.append(self._check_bid_ask_quality(snapshot))
        checks.append(self._check_volume_oi_quality(snapshot))
        checks.append(self._check_expiry_integrity(snapshot))
        checks.append(self._check_snapshot_alignment(snapshot))

        # Compute overall status
        has_fail = any(c.status == QualityStatus.FAIL for c in checks)
        has_warn = any(c.status == QualityStatus.WARN for c in checks)

        if has_fail:
            overall = QualityStatus.FAIL
        elif has_warn:
            overall = QualityStatus.WARN
        else:
            overall = QualityStatus.PASS

        # Quality score: fraction of passed checks (weighted)
        total = len(checks)
        passed = sum(1 for c in checks if c.status == QualityStatus.PASS)
        warned = sum(1 for c in checks if c.status == QualityStatus.WARN)
        quality_score = (passed + warned * 0.5) / max(total, 1)

        return QualityReport(
            snapshot_id=snapshot.snapshot_id,
            symbol=snapshot.symbol,
            overall_status=overall,
            quality_score=round(quality_score, 4),
            checks=tuple(checks),
        )

    # ── Check 1: Timestamp Freshness ───────────────────────────────────

    def _check_timestamp_freshness(self, snapshot: ChainSnapshot) -> QualityCheck:
        """Reject if spot or chain data is too old.

        Uses snapshot_timestamp (when we fetched) as primary freshness indicator.
        source_timestamp (exchange last-trade time) is only used for cross-source
        skew validation, not for age — it is unreliable during closed market.
        """
        now = datetime.now(timezone.utc)
        snap_age_ms = (now - snapshot.snapshot_timestamp).total_seconds() * 1000

        # Spot age = time since we fetched the spot (not exchange last-trade time)
        spot_age_ms = snap_age_ms
        if snapshot.spot_tick and snapshot.spot_tick.timestamp:
            spot_age_ms = (now - snapshot.spot_tick.timestamp).total_seconds() * 1000

        # Cross-source skew: spot fetch time vs chain fetch time
        skew_ms = 0.0
        if snapshot.spot_tick and snapshot.spot_tick.timestamp:
            skew_ms = abs(
                (snapshot.snapshot_timestamp - snapshot.spot_tick.timestamp).total_seconds() * 1000
            )

        issues: list[str] = []
        if spot_age_ms > self._cfg.max_spot_age_ms:
            issues.append(f"spot_age={spot_age_ms:.0f}ms > {self._cfg.max_spot_age_ms}ms")
        if snap_age_ms > self._cfg.max_chain_age_ms:
            issues.append(f"chain_age={snap_age_ms:.0f}ms > {self._cfg.max_chain_age_ms}ms")
        if skew_ms > self._cfg.max_cross_source_skew_ms:
            issues.append(f"skew={skew_ms:.0f}ms > {self._cfg.max_cross_source_skew_ms}ms")

        if issues:
            return QualityCheck(
                check_name="timestamp_freshness",
                status=QualityStatus.FAIL,
                threshold=f"spot<{self._cfg.max_spot_age_ms}ms, chain<{self._cfg.max_chain_age_ms}ms, skew<{self._cfg.max_cross_source_skew_ms}ms",
                observed="; ".join(issues),
                result=False,
                reason="Data too stale or timestamp skew too large",
            )

        return QualityCheck(
            check_name="timestamp_freshness",
            status=QualityStatus.PASS,
            threshold=f"spot<{self._cfg.max_spot_age_ms}ms, chain<{self._cfg.max_chain_age_ms}ms",
            observed=f"spot_age={spot_age_ms:.0f}ms, chain_age={snap_age_ms:.0f}ms, skew={skew_ms:.0f}ms",
            result=True,
        )

    # ── Check 2: Null / Missing Fields ─────────────────────────────────

    def _check_null_missing(self, snapshot: ChainSnapshot) -> QualityCheck:
        """Reject if critical fields missing; warn if optional fields missing."""
        if snapshot.spot_ltp <= 0:
            return QualityCheck(
                check_name="null_missing_fields",
                status=QualityStatus.FAIL,
                threshold="spot_ltp > 0",
                observed=f"spot_ltp={snapshot.spot_ltp}",
                result=False,
                reason="Spot LTP is zero or negative",
            )

        if not snapshot.rows:
            return QualityCheck(
                check_name="null_missing_fields",
                status=QualityStatus.FAIL,
                threshold="rows > 0",
                observed="0 rows",
                result=False,
                reason="Empty option chain",
            )

        critical_missing = 0
        optional_missing = 0
        for row in snapshot.rows:
            if row.strike <= 0 or row.ltp < 0:
                critical_missing += 1
            if row.iv is None:
                optional_missing += 1
            if row.oi is None:
                optional_missing += 1
            if row.bid is None or row.ask is None:
                optional_missing += 1

        if critical_missing > 0:
            return QualityCheck(
                check_name="null_missing_fields",
                status=QualityStatus.FAIL,
                threshold="0 critical missing",
                observed=f"{critical_missing} rows with invalid strike or negative LTP",
                result=False,
                reason="Critical fields missing in option rows",
            )

        if optional_missing > len(snapshot.rows):
            return QualityCheck(
                check_name="null_missing_fields",
                status=QualityStatus.WARN,
                threshold="optional fields present",
                observed=f"{optional_missing} optional field gaps across {len(snapshot.rows)} rows",
                result=True,
                reason="Some optional fields (IV, OI, bid/ask) missing",
            )

        return QualityCheck(
            check_name="null_missing_fields",
            status=QualityStatus.PASS,
            threshold="all critical fields present",
            observed=f"{len(snapshot.rows)} rows, {optional_missing} optional gaps",
            result=True,
        )

    # ── Check 3: Duplicate / Stale Snapshot ────────────────────────────

    def _check_duplicate_stale(
        self,
        snapshot: ChainSnapshot,
        prev_snapshot: Optional[ChainSnapshot],
    ) -> QualityCheck:
        """Detect if snapshot is identical to previous (stale data)."""
        current_hash = self._compute_snapshot_hash(snapshot)

        # Track hash history
        self._prev_hashes.append(current_hash)
        if len(self._prev_hashes) > self._max_stale + 1:
            self._prev_hashes = self._prev_hashes[-(self._max_stale + 1):]

        # Check how many consecutive identical hashes
        consecutive = 0
        for h in reversed(self._prev_hashes[:-1]):
            if h == current_hash:
                consecutive += 1
            else:
                break

        if consecutive >= self._max_stale:
            return QualityCheck(
                check_name="duplicate_stale_snapshot",
                status=QualityStatus.FAIL,
                threshold=f"< {self._max_stale} consecutive identical",
                observed=f"{consecutive} consecutive identical snapshots",
                result=False,
                reason=f"Snapshot unchanged for {consecutive} cycles — likely stale feed",
            )

        if consecutive >= self._max_stale // 2:
            return QualityCheck(
                check_name="duplicate_stale_snapshot",
                status=QualityStatus.WARN,
                threshold=f"< {self._max_stale} consecutive identical",
                observed=f"{consecutive} consecutive identical snapshots",
                result=True,
                reason="Snapshot partially stale — approaching threshold",
            )

        return QualityCheck(
            check_name="duplicate_stale_snapshot",
            status=QualityStatus.PASS,
            threshold=f"< {self._max_stale} consecutive identical",
            observed=f"{consecutive} consecutive identical" if consecutive > 0 else "fresh data",
            result=True,
        )

    # ── Check 4: Price Sanity ──────────────────────────────────────────

    def _check_price_sanity(self, snapshot: ChainSnapshot) -> list[QualityCheck]:
        """Validate LTP > 0, no negative vol/OI, intrinsic floor enforcement.

        Intrinsic floor check is limited to the configured strike window
        (ATM ± N steps), because deep ITM/OTM strikes with LTP=0 are normal
        when no one trades them — they should not trigger a FAIL.
        """
        checks: list[QualityCheck] = []
        spot = snapshot.spot_ltp
        eps = self._cfg.intrinsic_floor_epsilon
        step = self._strike_step
        window = self._window_size

        negative_ltp = 0
        negative_vol = 0
        negative_oi = 0
        floor_violations = 0
        floor_checked = 0
        total = len(snapshot.rows)

        for row in snapshot.rows:
            if row.ltp < 0:
                negative_ltp += 1
            if row.volume is not None and row.volume < 0:
                negative_vol += 1
            if row.oi is not None and row.oi < 0:
                negative_oi += 1

            # Intrinsic floor check — only within strike window (ATM ± N)
            # Epsilon scales with intrinsic: fixed floor for low intrinsic,
            # percentage-based for deep ITM (which routinely trades 1-3% below)
            if abs(row.strike - spot) <= step * window:
                floor_checked += 1
                if row.option_type == OptionType.CE:
                    intrinsic = max(spot - row.strike, 0)
                else:
                    intrinsic = max(row.strike - spot, 0)

                # Scale epsilon: max(fixed_eps, 3% of intrinsic)
                # Deep ITM options trade up to 3% below intrinsic normally
                scaled_eps = max(eps, intrinsic * 0.03)
                if row.ltp > 0 and row.ltp < intrinsic - scaled_eps:
                    floor_violations += 1

        # LTP check
        if negative_ltp > 0:
            checks.append(QualityCheck(
                check_name="price_sanity_ltp",
                status=QualityStatus.FAIL,
                threshold="LTP >= 0 for all rows",
                observed=f"{negative_ltp}/{total} rows with negative LTP",
                result=False,
                reason="Negative LTP values found",
            ))
        else:
            checks.append(QualityCheck(
                check_name="price_sanity_ltp",
                status=QualityStatus.PASS,
                threshold="LTP >= 0 for all rows",
                observed=f"all {total} rows valid",
                result=True,
            ))

        # Volume/OI check
        if negative_vol > 0 or negative_oi > 0:
            checks.append(QualityCheck(
                check_name="price_sanity_vol_oi",
                status=QualityStatus.FAIL,
                threshold="volume >= 0, OI >= 0",
                observed=f"neg_vol={negative_vol}, neg_oi={negative_oi}",
                result=False,
                reason="Negative volume or OI values",
            ))
        else:
            checks.append(QualityCheck(
                check_name="price_sanity_vol_oi",
                status=QualityStatus.PASS,
                threshold="volume >= 0, OI >= 0",
                observed="all valid",
                result=True,
            ))

        # Intrinsic floor check (within window only)
        if floor_violations > 0:
            status = QualityStatus.FAIL if floor_violations > floor_checked * 0.1 else QualityStatus.WARN
            checks.append(QualityCheck(
                check_name="price_sanity_intrinsic_floor",
                status=status,
                threshold=f"LTP >= intrinsic - {eps} (ATM ± {window} strikes)",
                observed=f"{floor_violations}/{floor_checked} violations in window",
                result=status != QualityStatus.FAIL,
                reason=f"{floor_violations} rows below intrinsic floor in ATM window",
            ))
        else:
            checks.append(QualityCheck(
                check_name="price_sanity_intrinsic_floor",
                status=QualityStatus.PASS,
                threshold=f"LTP >= intrinsic - {eps} (ATM ± {window} strikes)",
                observed=f"all {floor_checked} rows in window above floor",
                result=True,
            ))

        return checks

    # ── Check 5: Strike Continuity ─────────────────────────────────────

    def _check_strike_continuity(self, snapshot: ChainSnapshot) -> QualityCheck:
        """Check strike intervals are consistent and no central strikes missing."""
        strikes = sorted(set(r.strike for r in snapshot.rows))
        if len(strikes) < 3:
            return QualityCheck(
                check_name="strike_continuity",
                status=QualityStatus.FAIL,
                threshold=">= 3 strikes",
                observed=f"{len(strikes)} strikes",
                result=False,
                reason="Too few strikes in chain",
            )

        step = self._strike_step
        spot = snapshot.spot_ltp
        gaps: list[str] = []
        atm_present = False

        for i in range(len(strikes) - 1):
            actual_gap = strikes[i + 1] - strikes[i]
            if abs(actual_gap - step) > 1:
                gaps.append(f"{strikes[i]}-{strikes[i+1]} (gap={actual_gap})")

        # Check ATM neighborhood exists
        nearest_strike = min(strikes, key=lambda k: abs(k - spot))
        if abs(nearest_strike - spot) <= step:
            atm_present = True

        if not atm_present:
            return QualityCheck(
                check_name="strike_continuity",
                status=QualityStatus.FAIL,
                threshold=f"ATM strike within {step} of spot",
                observed=f"nearest={nearest_strike}, spot={spot}, gap={abs(nearest_strike - spot):.1f}",
                result=False,
                reason="ATM neighborhood missing from chain",
            )

        if len(gaps) > len(strikes) * 0.1:
            return QualityCheck(
                check_name="strike_continuity",
                status=QualityStatus.WARN,
                threshold=f"consistent {step}-point spacing",
                observed=f"{len(gaps)} gaps: {gaps[:3]}{'...' if len(gaps) > 3 else ''}",
                result=True,
                reason="Irregular strike spacing detected",
            )

        return QualityCheck(
            check_name="strike_continuity",
            status=QualityStatus.PASS,
            threshold=f"consistent {step}-point spacing, ATM present",
            observed=f"{len(strikes)} strikes, {len(gaps)} gaps, ATM={nearest_strike}",
            result=True,
        )

    # ── Check 6: Bid/Ask Quality ───────────────────────────────────────

    def _check_bid_ask_quality(self, snapshot: ChainSnapshot) -> QualityCheck:
        """Check spread quality where bid/ask data exists."""
        rows_with_book = [
            r for r in snapshot.rows
            if r.bid is not None and r.ask is not None and r.bid > 0 and r.ask > 0
        ]

        if not rows_with_book:
            return QualityCheck(
                check_name="bid_ask_quality",
                status=QualityStatus.WARN,
                threshold="bid/ask data available",
                observed="no bid/ask data in chain",
                result=True,
                reason="Book data not available — cannot assess spread quality",
            )

        wide_spread = 0
        inverted = 0
        ghost = 0
        max_threshold = self._cfg.max_spread_pct

        for row in rows_with_book:
            if row.ask < row.bid:
                inverted += 1
                continue
            mid = (row.bid + row.ask) / 2
            if mid > 0:
                spread_pct = ((row.ask - row.bid) / mid) * 100
                if spread_pct > max_threshold:
                    wide_spread += 1
            if row.bid_qty is not None and row.bid_qty < self._cfg.min_bid_qty:
                ghost += 1
            if row.ask_qty is not None and row.ask_qty < self._cfg.min_ask_qty:
                ghost += 1

        total = len(rows_with_book)
        issues: list[str] = []
        if inverted > 0:
            issues.append(f"{inverted} inverted (ask<bid)")
        if wide_spread > 0:
            issues.append(f"{wide_spread} wide (>{max_threshold}%)")
        if ghost > 0:
            issues.append(f"{ghost} ghost liquidity")

        if inverted > 0:
            return QualityCheck(
                check_name="bid_ask_quality",
                status=QualityStatus.FAIL,
                threshold=f"spread<{max_threshold}%, no inversions",
                observed="; ".join(issues),
                result=False,
                reason="Inverted bid/ask found",
            )

        if wide_spread > total * 0.3:
            return QualityCheck(
                check_name="bid_ask_quality",
                status=QualityStatus.WARN,
                threshold=f"spread<{max_threshold}%",
                observed="; ".join(issues),
                result=True,
                reason="Many strikes with wide spreads",
            )

        return QualityCheck(
            check_name="bid_ask_quality",
            status=QualityStatus.PASS,
            threshold=f"spread<{max_threshold}%, no inversions",
            observed=f"{total} rows checked, {', '.join(issues) if issues else 'all clean'}",
            result=True,
        )

    # ── Check 7: Volume / OI Quality ───────────────────────────────────

    def _check_volume_oi_quality(self, snapshot: ChainSnapshot) -> QualityCheck:
        """Flag low-confidence rows with insufficient volume or OI."""
        low_vol = 0
        low_oi = 0
        no_trade = 0
        total = len(snapshot.rows)

        for row in snapshot.rows:
            if row.volume is not None and row.volume < self._cfg.min_volume:
                low_vol += 1
            if row.oi is not None and row.oi < self._cfg.min_oi:
                low_oi += 1
            if row.volume is not None and row.volume == 0 and row.ltp == 0:
                no_trade += 1

        # Only warn/fail based on ATM region (far OTM naturally has low volume)
        spot = snapshot.spot_ltp
        step = self._strike_step
        atm_rows = [
            r for r in snapshot.rows
            if abs(r.strike - spot) <= step * 4
        ]
        atm_low_vol = sum(
            1 for r in atm_rows
            if r.volume is not None and r.volume < self._cfg.min_volume
        )

        if atm_low_vol > len(atm_rows) * 0.5 and atm_rows:
            return QualityCheck(
                check_name="volume_oi_quality",
                status=QualityStatus.WARN,
                threshold=f"ATM vol>={self._cfg.min_volume}, OI>={self._cfg.min_oi}",
                observed=f"ATM: {atm_low_vol}/{len(atm_rows)} low vol. Total: {low_vol}/{total} low vol, {low_oi}/{total} low OI",
                result=True,
                reason="Low volume in ATM region — possible thin market",
            )

        return QualityCheck(
            check_name="volume_oi_quality",
            status=QualityStatus.PASS,
            threshold=f"ATM vol>={self._cfg.min_volume}",
            observed=f"ATM: {len(atm_rows)} rows checked. Total: {low_vol} low vol, {low_oi} low OI",
            result=True,
        )

    # ── Check 8: Expiry Integrity ──────────────────────────────────────

    def _check_expiry_integrity(self, snapshot: ChainSnapshot) -> QualityCheck:
        """Ensure all rows belong to the same expiry — no mixed contamination."""
        expiries = set(r.expiry for r in snapshot.rows if r.expiry)

        if len(expiries) == 0:
            return QualityCheck(
                check_name="expiry_integrity",
                status=QualityStatus.WARN,
                threshold="single expiry in chain",
                observed="no expiry data in rows",
                result=True,
                reason="Expiry field not populated",
            )

        if len(expiries) > 1:
            return QualityCheck(
                check_name="expiry_integrity",
                status=QualityStatus.FAIL,
                threshold="single expiry in chain",
                observed=f"{len(expiries)} expiries found: {sorted(expiries)}",
                result=False,
                reason="Mixed-expiry contamination in chain",
            )

        return QualityCheck(
            check_name="expiry_integrity",
            status=QualityStatus.PASS,
            threshold="single expiry in chain",
            observed=f"expiry={expiries.pop()}",
            result=True,
        )

    # ── Check 9: Snapshot Alignment ────────────────────────────────────

    def _check_snapshot_alignment(self, snapshot: ChainSnapshot) -> QualityCheck:
        """Validate spot + chain timestamp alignment per configured mode."""
        if not snapshot.spot_tick:
            if self._snapshot_mode == SnapshotMode.STRICT:
                return QualityCheck(
                    check_name="snapshot_alignment",
                    status=QualityStatus.FAIL,
                    threshold="spot tick present (STRICT mode)",
                    observed="no spot tick attached",
                    result=False,
                    reason="STRICT mode requires spot tick with chain",
                )
            return QualityCheck(
                check_name="snapshot_alignment",
                status=QualityStatus.WARN,
                threshold="spot tick present",
                observed="no spot tick — using chain spot_ltp",
                result=True,
                reason="TOLERANT mode: using chain-embedded spot",
            )

        skew_ms = abs(
            (snapshot.snapshot_timestamp - snapshot.spot_tick.timestamp).total_seconds() * 1000
        )

        if self._snapshot_mode == SnapshotMode.STRICT and skew_ms > self._cfg.max_cross_source_skew_ms:
            return QualityCheck(
                check_name="snapshot_alignment",
                status=QualityStatus.FAIL,
                threshold=f"skew < {self._cfg.max_cross_source_skew_ms}ms (STRICT)",
                observed=f"skew={skew_ms:.0f}ms",
                result=False,
                reason="Spot-chain timestamp mismatch exceeds STRICT threshold",
            )

        return QualityCheck(
            check_name="snapshot_alignment",
            status=QualityStatus.PASS,
            threshold=f"skew < {self._cfg.max_cross_source_skew_ms}ms ({self._snapshot_mode.value})",
            observed=f"skew={skew_ms:.0f}ms",
            result=True,
        )

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _compute_snapshot_hash(snapshot: ChainSnapshot) -> str:
        """Compute deterministic hash of snapshot data for staleness detection."""
        data = []
        for row in sorted(snapshot.rows, key=lambda r: (r.strike, r.option_type.value)):
            data.append(f"{row.strike}:{row.option_type.value}:{row.ltp}:{row.volume}:{row.oi}")
        content = f"{snapshot.spot_ltp}|{'|'.join(data)}"
        return hashlib.md5(content.encode()).hexdigest()[:16]
