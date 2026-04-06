"""Tradability validator — per-strike executable quality gate.

Runs BEFORE a strike enters the scoring pipeline. A strike that fails
tradability is never scored — it's rejected with an explicit reason.

Checks:
1. bid > 0 (if require_bid)
2. ask > 0 (if require_ask)
3. spread_pct <= threshold
4. bid_qty >= min
5. ask_qty >= min
6. volume >= min_recent_volume
7. last_trade_age <= threshold (optional, disabled by default)

Returns a TradabilityResult per strike with pass/fail + all check details.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..config import TradabilityConfig
from ..models import OptionRow, OptionType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradabilityCheck:
    """Result of a single tradability check."""
    name: str
    passed: bool
    observed: str
    threshold: str


@dataclass(frozen=True)
class TradabilityResult:
    """Aggregate tradability result for one strike + side."""
    strike: float
    option_type: OptionType
    tradable: bool
    checks: tuple[TradabilityCheck, ...] = ()
    rejection_primary: Optional[str] = None
    rejection_all: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "strike": self.strike,
            "option_type": self.option_type.value,
            "tradable": self.tradable,
            "rejection_primary": self.rejection_primary,
            "rejection_all": list(self.rejection_all),
            "checks": [
                {"name": c.name, "passed": c.passed, "observed": c.observed, "threshold": c.threshold}
                for c in self.checks
            ],
        }


def check_tradability(
    row: OptionRow,
    config: TradabilityConfig,
    current_time: Optional[datetime] = None,
) -> TradabilityResult:
    """Run all tradability checks on a single option row.

    Args:
        row: The option contract to validate.
        config: Tradability thresholds.
        current_time: For last_trade_age check (defaults to now).

    Returns:
        TradabilityResult with per-check details.
    """
    checks: list[TradabilityCheck] = []
    failures: list[str] = []

    # 1. Bid > 0
    if config.require_bid:
        bid_ok = row.bid is not None and row.bid > 0
        checks.append(TradabilityCheck(
            name="bid_present",
            passed=bid_ok,
            observed=f"bid={row.bid}" if row.bid is not None else "bid=None",
            threshold="bid > 0",
        ))
        if not bid_ok:
            failures.append("bid_missing")

    # 2. Ask > 0
    if config.require_ask:
        ask_ok = row.ask is not None and row.ask > 0
        checks.append(TradabilityCheck(
            name="ask_present",
            passed=ask_ok,
            observed=f"ask={row.ask}" if row.ask is not None else "ask=None",
            threshold="ask > 0",
        ))
        if not ask_ok:
            failures.append("ask_missing")

    # 3. Spread %
    if row.bid is not None and row.ask is not None and row.bid > 0 and row.ask > 0:
        mid = (row.bid + row.ask) / 2
        spread_pct = ((row.ask - row.bid) / mid) * 100 if mid > 0 else 999
        spread_ok = spread_pct <= config.max_spread_pct
        checks.append(TradabilityCheck(
            name="spread_pct",
            passed=spread_ok,
            observed=f"spread={spread_pct:.2f}%",
            threshold=f"<= {config.max_spread_pct}%",
        ))
        if not spread_ok:
            failures.append(f"spread_wide({spread_pct:.1f}%)")
    else:
        # No bid/ask — can't check spread, skip (don't fail on missing data)
        checks.append(TradabilityCheck(
            name="spread_pct",
            passed=True,
            observed="no bid/ask data — skipped",
            threshold=f"<= {config.max_spread_pct}%",
        ))

    # 4. Bid quantity
    if config.min_bid_qty > 0:
        bq = row.bid_qty or 0
        bq_ok = bq >= config.min_bid_qty
        checks.append(TradabilityCheck(
            name="bid_qty",
            passed=bq_ok,
            observed=f"bid_qty={bq}",
            threshold=f">= {config.min_bid_qty}",
        ))
        if not bq_ok:
            failures.append(f"bid_qty_low({bq})")

    # 5. Ask quantity
    if config.min_ask_qty > 0:
        aq = row.ask_qty or 0
        aq_ok = aq >= config.min_ask_qty
        checks.append(TradabilityCheck(
            name="ask_qty",
            passed=aq_ok,
            observed=f"ask_qty={aq}",
            threshold=f">= {config.min_ask_qty}",
        ))
        if not aq_ok:
            failures.append(f"ask_qty_low({aq})")

    # 6. Volume
    if config.min_recent_volume > 0:
        vol = row.volume or 0
        vol_ok = vol >= config.min_recent_volume
        checks.append(TradabilityCheck(
            name="volume",
            passed=vol_ok,
            observed=f"volume={vol:,}",
            threshold=f">= {config.min_recent_volume:,}",
        ))
        if not vol_ok:
            failures.append(f"volume_low({vol})")

    # 7. Last trade age (optional — disabled if threshold=0)
    if config.max_last_trade_age_seconds > 0 and row.last_trade_time is not None:
        now = current_time or datetime.now(timezone.utc)
        age_seconds = (now - row.last_trade_time).total_seconds()
        age_ok = age_seconds <= config.max_last_trade_age_seconds
        checks.append(TradabilityCheck(
            name="last_trade_age",
            passed=age_ok,
            observed=f"age={age_seconds:.0f}s",
            threshold=f"<= {config.max_last_trade_age_seconds}s",
        ))
        if not age_ok:
            failures.append(f"stale_trade({age_seconds:.0f}s)")

    tradable = len(failures) == 0
    return TradabilityResult(
        strike=row.strike,
        option_type=row.option_type,
        tradable=tradable,
        checks=tuple(checks),
        rejection_primary=failures[0] if failures else None,
        rejection_all=tuple(failures),
    )


def filter_tradable_candidates(
    rows: list[OptionRow],
    config: TradabilityConfig,
    spot: float,
    band_min: float,
    band_max: float,
    otm_min: int,
) -> tuple[list[OptionRow], list[TradabilityResult]]:
    """Filter option rows to only tradable candidates.

    Applies tradability checks + premium band + OTM distance filters.
    Returns both the passing rows and full rejection audit for all scanned strikes.

    Args:
        rows: All option rows from the chain.
        config: Tradability config.
        spot: Current spot price.
        band_min: Min premium for lottery band.
        band_max: Max premium for lottery band.
        otm_min: Min OTM distance in points.

    Returns:
        (tradable_rows, all_audits) — passing rows + full rejection audit.
    """
    tradable: list[OptionRow] = []
    audits: list[TradabilityResult] = []

    for row in rows:
        # Pre-filter: only consider OTM strikes in premium band
        if row.option_type == OptionType.CE:
            if row.strike <= spot:
                continue  # ITM call — skip
            if abs(row.strike - spot) < otm_min:
                continue  # too close to ATM
            if not (band_min <= row.ltp <= band_max):
                continue  # outside premium band
        elif row.option_type == OptionType.PE:
            if row.strike >= spot:
                continue  # ITM put — skip
            if abs(row.strike - spot) < otm_min:
                continue
            if not (band_min <= row.ltp <= band_max):
                continue
        else:
            continue

        # Run tradability checks
        result = check_tradability(row, config)
        audits.append(result)

        if result.tradable:
            tradable.append(row)

    return tradable, audits
