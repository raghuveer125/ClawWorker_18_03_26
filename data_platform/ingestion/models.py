"""
Typed data models for canonical Layer 1 (Data Ingestion Service).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


OptionSide = Literal["CE", "PE"]


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    index: str
    ltp: float
    bid: float
    ask: float
    timestamp: datetime
    change: float = 0.0
    last_traded_qty: int = 0


@dataclass(frozen=True)
class OptionContractSnapshot:
    index: str
    symbol: str
    strike: float
    side: OptionSide
    ltp: float
    oi: int
    oi_change: int
    volume: int
    volume_delta: int
    iv: float
    expiry: str
    timestamp: datetime
    bid: float = 0.0
    ask: float = 0.0
    change: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0


@dataclass(frozen=True)
class OptionChainSnapshot:
    index: str
    timestamp: datetime
    contracts: list[OptionContractSnapshot]
    underlying_ltp: float = 0.0
    atm_strike: float = 0.0
    depth: int = 0
    is_expiry_day: bool = False
    session_state: str = "regular"


@dataclass(frozen=True)
class VIXSnapshot:
    value: float
    timestamp: datetime


@dataclass(frozen=True)
class FuturesSnapshot:
    symbol: str
    index: str
    ltp: float
    timestamp: datetime
    change: float = 0.0
    underlying_ltp: float = 0.0
    basis: float = 0.0
    basis_pct: float = 0.0
