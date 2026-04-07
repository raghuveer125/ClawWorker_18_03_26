"""
Raw broker payload normalization for canonical Layer 1 (Data Ingestion).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from data_platform.ingestion.models import (
    FuturesSnapshot,
    OptionChainSnapshot,
    OptionContractSnapshot,
    QuoteSnapshot,
    VIXSnapshot,
)


class DataNormalizer:
    """Converts provider-specific payloads into stable typed snapshots."""

    @staticmethod
    def _coalesce(raw: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in raw and raw[key] is not None:
                return raw[key]
        return default

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            # Accept epoch seconds and milliseconds.
            ts = value / 1000.0 if value > 10_000_000_000 else float(value)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                numeric = int(stripped)
                ts = numeric / 1000.0 if numeric > 10_000_000_000 else float(numeric)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        raise ValueError("timestamp missing or invalid")

    @classmethod
    def normalize_quote(cls, index: str, raw: Mapping[str, Any]) -> QuoteSnapshot:
        symbol = str(cls._coalesce(raw, "symbol", "ticker", default=index))
        _ltp_raw = cls._coalesce(raw, "ltp", "last_price", "lastTradedPrice", "lp", "close", "cmd", default=None)
        if _ltp_raw is None:
            raise ValueError(f"normalize_quote: ltp missing for index={index}, keys={list(raw.keys())}")
        ltp = float(_ltp_raw)
        bid = float(cls._coalesce(raw, "bid", "bid_price", default=ltp))
        ask = float(cls._coalesce(raw, "ask", "ask_price", default=ltp))
        bid = bid if bid > 0 else ltp
        ask = ask if ask > 0 else ltp
        change = float(cls._coalesce(raw, "change", "net_change", "ch", default=0.0))
        last_traded_qty = int(cls._coalesce(raw, "ltq", "last_traded_qty", default=0))
        ts = cls._parse_timestamp(cls._coalesce(raw, "timestamp", "ts", "t", "tt"))
        if ts == ts.replace(hour=0, minute=0, second=0, microsecond=0):
            ts = datetime.now(timezone.utc)

        if ltp <= 0 or bid < 0 or ask < 0:
            raise ValueError("invalid quote prices")
        return QuoteSnapshot(
            symbol=symbol,
            index=index,
            ltp=ltp,
            bid=bid,
            ask=ask,
            timestamp=ts,
            change=change,
            last_traded_qty=last_traded_qty,
        )

    @classmethod
    def normalize_option_contract(
        cls,
        index: str,
        raw: Mapping[str, Any],
        default_ts: datetime,
    ) -> OptionContractSnapshot:
        side = str(cls._coalesce(raw, "side", "option_type", default="")).upper()
        if side not in {"CE", "PE"}:
            raise ValueError("option side must be CE or PE")

        strike = float(cls._coalesce(raw, "strike", "strike_price"))
        ltp = float(cls._coalesce(raw, "ltp", "last_price", default=0.0))
        oi = int(cls._coalesce(raw, "oi", "open_interest", default=0))
        oi_change = int(cls._coalesce(raw, "oich", "oi_change", default=0))
        volume = int(cls._coalesce(raw, "volume", "vol", default=0))
        volume_delta = int(cls._coalesce(raw, "volume_delta", "volume_change", "vol_ch", default=0))
        iv = float(cls._coalesce(raw, "iv", "implied_volatility", default=0.0))
        bid = float(cls._coalesce(raw, "bid", "bid_price", default=0.0))
        ask = float(cls._coalesce(raw, "ask", "ask_price", default=0.0))
        change = float(cls._coalesce(raw, "change", "net_change", "ch", default=0.0))
        delta = float(cls._coalesce(raw, "delta", default=0.0))
        gamma = float(cls._coalesce(raw, "gamma", default=0.0))
        theta = float(cls._coalesce(raw, "theta", default=0.0))
        vega = float(cls._coalesce(raw, "vega", default=0.0))
        symbol = str(cls._coalesce(raw, "symbol", default=f"{index}-{strike}-{side}"))
        expiry = str(cls._coalesce(raw, "expiry", "expiry_date", default=""))
        ts_raw = cls._coalesce(raw, "timestamp", "ts", "t", default=default_ts)
        ts = cls._parse_timestamp(ts_raw)

        if strike <= 0 or ltp < 0 or oi < 0 or volume < 0 or iv < 0:
            raise ValueError("invalid option contract values")

        return OptionContractSnapshot(
            index=index,
            symbol=symbol,
            strike=strike,
            side=side,
            ltp=ltp,
            oi=oi,
            oi_change=oi_change,
            volume=volume,
            volume_delta=volume_delta,
            iv=iv,
            expiry=expiry,
            timestamp=ts,
            bid=bid,
            ask=ask,
            change=change,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
        )

    @staticmethod
    def _infer_atm_strike(
        contracts: Sequence[OptionContractSnapshot],
        underlying_ltp: float,
    ) -> float:
        if not contracts or underlying_ltp <= 0:
            return 0.0
        return min(contracts, key=lambda c: abs(c.strike - underlying_ltp)).strike

    @staticmethod
    def _apply_atm_depth(
        contracts: Sequence[OptionContractSnapshot],
        atm_strike: float,
        atm_depth: int | None,
    ) -> list[OptionContractSnapshot]:
        if not contracts:
            return []
        if atm_depth is None or atm_depth < 0:
            return list(contracts)
        if atm_depth == 0:
            selected_strikes = {atm_strike}
        else:
            unique_strikes = sorted({c.strike for c in contracts}, key=lambda s: abs(s - atm_strike))
            selected_strikes = set(unique_strikes[: (atm_depth * 2) + 1])
        return [c for c in contracts if c.strike in selected_strikes]

    @classmethod
    def normalize_option_chain(
        cls,
        index: str,
        raw_contracts: Sequence[Mapping[str, Any]],
        as_of: Any,
        underlying_ltp: float = 0.0,
        atm_strike: float = 0.0,
        atm_depth: int | None = None,
        is_expiry_day: bool = False,
        session_state: str = "regular",
    ) -> OptionChainSnapshot:
        ts = cls._parse_timestamp(as_of)
        contracts_all = [
            cls.normalize_option_contract(index=index, raw=row, default_ts=ts)
            for row in raw_contracts
        ]
        resolved_atm = atm_strike or cls._infer_atm_strike(contracts_all, underlying_ltp)
        filtered_contracts = cls._apply_atm_depth(contracts_all, resolved_atm, atm_depth)
        return OptionChainSnapshot(
            index=index,
            timestamp=ts,
            contracts=filtered_contracts,
            underlying_ltp=underlying_ltp,
            atm_strike=resolved_atm,
            depth=-1 if atm_depth is None else atm_depth,
            is_expiry_day=is_expiry_day,
            session_state=session_state or "regular",
        )

    @classmethod
    def normalize_vix(cls, raw: Mapping[str, Any]) -> VIXSnapshot:
        _vix_raw = cls._coalesce(raw, "vix", "value", "ltp", "lp", "close", default=None)
        if _vix_raw is None:
            raise ValueError(f"normalize_vix: value missing, keys={list(raw.keys())}")
        value = float(_vix_raw)
        ts = cls._parse_timestamp(cls._coalesce(raw, "timestamp", "ts", "t", "tt"))
        if value <= 0:
            raise ValueError("invalid vix value")
        return VIXSnapshot(value=value, timestamp=ts)

    @classmethod
    def normalize_futures(cls, index: str, raw: Mapping[str, Any]) -> FuturesSnapshot:
        symbol = str(cls._coalesce(raw, "symbol", "ticker", default=f"{index}-FUT"))
        _ltp_raw = cls._coalesce(raw, "ltp", "last_price", "lp", "close", default=None)
        if _ltp_raw is None:
            raise ValueError(f"normalize_futures: ltp missing for index={index}, keys={list(raw.keys())}")
        ltp = float(_ltp_raw)
        change = float(cls._coalesce(raw, "change", "net_change", "ch", default=0.0))
        underlying_ltp = float(cls._coalesce(raw, "underlying_ltp", "spot_ltp", default=0.0))
        ts = cls._parse_timestamp(cls._coalesce(raw, "timestamp", "ts", "t", "tt"))
        if ts == ts.replace(hour=0, minute=0, second=0, microsecond=0):
            ts = datetime.now(timezone.utc)
        if ltp <= 0:
            raise ValueError("invalid futures price")
        basis = ltp - underlying_ltp if underlying_ltp > 0 else 0.0
        basis_pct = ((basis / underlying_ltp) * 100.0) if underlying_ltp > 0 else 0.0
        return FuturesSnapshot(
            symbol=symbol,
            index=index,
            ltp=ltp,
            timestamp=ts,
            change=change,
            underlying_ltp=underlying_ltp,
            basis=basis,
            basis_pct=basis_pct,
        )
