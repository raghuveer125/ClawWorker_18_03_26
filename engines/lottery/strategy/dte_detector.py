"""DTE detector — auto-select strategy profile from exchange calendar.

Primary source: shared_project_engine/indices/ exchange calendar
Secondary: manual config override
Tertiary: FYERS chain metadata (sanity check only)

Detects:
- Days to expiry (DTE) for current symbol
- Whether today is expiry day
- Auto-selects strategy profile: PRE_EXPIRY / DTE1_HYBRID / EXPIRY_DAY
"""

import logging
from datetime import date, datetime, timezone
from typing import Optional

from .profiles import StrategyMode, StrategyProfile, get_profile_for_dte

logger = logging.getLogger(__name__)


class DTEDetector:
    """Detects days-to-expiry and auto-selects strategy profile.

    Uses the exchange expiry calendar as the source of truth.
    """

    def __init__(self, symbol: str, manual_override: Optional[StrategyMode] = None) -> None:
        self._symbol = symbol
        self._manual_override = manual_override
        self._dte: Optional[int] = None
        self._expiry_date: Optional[str] = None
        self._is_expiry_day: bool = False
        self._profile: Optional[StrategyProfile] = None
        self._source: str = "unknown"

    @property
    def dte(self) -> Optional[int]:
        return self._dte

    @property
    def expiry_date(self) -> Optional[str]:
        return self._expiry_date

    @property
    def is_expiry_day(self) -> bool:
        return self._is_expiry_day

    @property
    def profile(self) -> Optional[StrategyProfile]:
        return self._profile

    @property
    def source(self) -> str:
        return self._source

    def detect(self) -> StrategyProfile:
        """Detect DTE and return the appropriate strategy profile.

        Returns:
            Selected StrategyProfile.
        """
        # Manual override takes priority
        if self._manual_override:
            from .profiles import get_profile
            self._profile = get_profile(self._manual_override)
            self._source = "manual_override"
            logger.info(
                "DTE: manual override → %s for %s",
                self._manual_override.value, self._symbol,
            )
            return self._profile

        # Try exchange calendar
        dte = self._detect_from_exchange_calendar()

        # Fallback: FYERS chain metadata (sanity check)
        if dte is None:
            dte = self._detect_from_fyers()

        # Final fallback: assume pre-expiry
        if dte is None:
            dte = 5
            self._source = "fallback_default"
            logger.warning(
                "DTE: could not determine for %s — defaulting to DTE=%d",
                self._symbol, dte,
            )

        self._dte = dte
        self._is_expiry_day = dte == 0
        self._profile = get_profile_for_dte(dte)

        logger.info(
            "DTE: %s DTE=%d expiry=%s is_expiry=%s profile=%s (source=%s)",
            self._symbol, dte,
            self._expiry_date or "unknown",
            self._is_expiry_day,
            self._profile.mode.value,
            self._source,
        )

        return self._profile

    def _detect_from_exchange_calendar(self) -> Optional[int]:
        """Get DTE from shared_project_engine exchange calendar."""
        try:
            from shared_project_engine.indices.config import (
                get_expiry_snapshot,
                is_expiry_today,
            )

            # Check if today is expiry
            cal_key = self._symbol_to_calendar_key()
            if is_expiry_today(cal_key):
                self._is_expiry_day = True
                self._expiry_date = date.today().isoformat()
                self._source = "exchange_calendar"
                return 0

            # Get next expiry date
            snapshot = get_expiry_snapshot(use_live=False)
            schedule = snapshot.get("schedule", {})
            index_data = schedule.get(cal_key, {})

            expiry_str = index_data.get("next_expiry")
            if expiry_str:
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                today = date.today()
                dte = (expiry_date - today).days

                if dte < 0:
                    # Expiry already passed — calendar might be stale
                    logger.warning("Exchange calendar expiry %s is in the past", expiry_str)
                    return None

                self._expiry_date = expiry_str
                self._source = "exchange_calendar"
                return dte

        except ImportError:
            logger.debug("shared_project_engine not available for DTE detection")
        except Exception as e:
            logger.warning("Exchange calendar DTE detection failed: %s", e)

        return None

    def _detect_from_fyers(self) -> Optional[int]:
        """Fallback: estimate DTE from FYERS weekday-based logic."""
        try:
            _weekday_map = {
                "NIFTY": 3,     # Thursday
                "NIFTY50": 3,
                "SENSEX": 4,    # Friday
            }

            cal_key = self._symbol.upper()
            weekday = _weekday_map.get(cal_key)
            if weekday is None:
                return None

            today = date.today()
            days = (weekday - today.weekday()) % 7
            if days == 0:
                self._expiry_date = today.isoformat()
                self._source = "weekday_estimate"
                return 0

            from datetime import timedelta
            exp = today + timedelta(days=days)
            self._expiry_date = exp.isoformat()
            self._source = "weekday_estimate"
            return days

        except Exception as e:
            logger.warning("FYERS weekday DTE fallback failed: %s", e)
            return None

    def _symbol_to_calendar_key(self) -> str:
        """Map our symbol to the exchange calendar key."""
        _map = {
            "NIFTY": "NIFTY50",
            "NIFTY50": "NIFTY50",
            "BANKNIFTY": "BANKNIFTY",
            "FINNIFTY": "FINNIFTY",
            "SENSEX": "SENSEX",
            "MIDCPNIFTY": "MIDCPNIFTY",
        }
        return _map.get(self._symbol.upper(), self._symbol.upper())

    def to_dict(self) -> dict:
        """Serialize for API/dashboard."""
        return {
            "symbol": self._symbol,
            "dte": self._dte,
            "expiry_date": self._expiry_date,
            "is_expiry_day": self._is_expiry_day,
            "profile": self._profile.mode.value if self._profile else None,
            "profile_label": self._profile.label if self._profile else None,
            "source": self._source,
        }
