"""Data models for the Lottery pipeline.

All models are frozen dataclasses — immutable after creation.
Symbol/instrument is never hardcoded; always passed from config or data.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ── Enums ──────────────────────────────────────────────────────────────────

class OptionType(Enum):
    CE = "CE"
    PE = "PE"


class Side(Enum):
    CE = "CE"
    PE = "PE"


class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class QualityStatus(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class SignalValidity(Enum):
    VALID = "VALID"
    INVALID = "INVALID"


class MachineState(Enum):
    IDLE = "IDLE"
    ZONE_ACTIVE_CE = "ZONE_ACTIVE_CE"
    ZONE_ACTIVE_PE = "ZONE_ACTIVE_PE"
    CANDIDATE_FOUND = "CANDIDATE_FOUND"
    IN_TRADE = "IN_TRADE"
    EXIT_PENDING = "EXIT_PENDING"
    COOLDOWN = "COOLDOWN"


class RejectionReason(Enum):
    DATA_QUALITY_FAIL = "DATA_QUALITY_FAIL"
    STALE_DATA = "STALE_DATA"
    ZONE_INACTIVE = "ZONE_INACTIVE"
    NO_BAND_CANDIDATE = "NO_BAND_CANDIDATE"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    LIQUIDITY_TOO_LOW = "LIQUIDITY_TOO_LOW"
    RISK_REJECTION = "RISK_REJECTION"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    TIME_FILTER = "TIME_FILTER"
    INSUFFICIENT_OTM_POINTS = "INSUFFICIENT_OTM_POINTS"
    MAX_DAILY_TRADES = "MAX_DAILY_TRADES"
    MAX_CONSECUTIVE_LOSSES = "MAX_CONSECUTIVE_LOSSES"
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS"


class ExitReason(Enum):
    STOP_LOSS = "STOP_LOSS"
    TARGET_1 = "TARGET_1"
    TARGET_2 = "TARGET_2"
    TARGET_3 = "TARGET_3"
    TIME_STOP = "TIME_STOP"
    EOD_EXIT = "EOD_EXIT"
    TRAILING_STOP = "TRAILING_STOP"
    INVALIDATION = "INVALIDATION"
    MANUAL = "MANUAL"


# ── Market Data Models ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class UnderlyingTick:
    """Spot price snapshot for any underlying instrument."""
    symbol: str
    exchange: str
    ltp: float
    timestamp: datetime
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    prev_close: Optional[float] = None
    source_timestamp: Optional[datetime] = None
    ingested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class OptionRow:
    """Single option contract row from the chain."""
    symbol: str
    expiry: str
    strike: float
    option_type: OptionType
    ltp: float
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[int] = None
    oi: Optional[int] = None
    oi_change: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    iv: Optional[float] = None
    last_trade_time: Optional[datetime] = None
    source_timestamp: Optional[datetime] = None
    ingested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ChainSnapshot:
    """Complete option chain snapshot for one symbol + expiry."""
    snapshot_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    symbol: str = ""
    expiry: str = ""
    spot_ltp: float = 0.0
    snapshot_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rows: tuple[OptionRow, ...] = ()
    spot_tick: Optional[UnderlyingTick] = None

    @property
    def call_rows(self) -> tuple[OptionRow, ...]:
        return tuple(r for r in self.rows if r.option_type == OptionType.CE)

    @property
    def put_rows(self) -> tuple[OptionRow, ...]:
        return tuple(r for r in self.rows if r.option_type == OptionType.PE)

    @property
    def strikes(self) -> tuple[float, ...]:
        return tuple(sorted(set(r.strike for r in self.rows)))


# ── Calculated Models ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class CalculatedRow:
    """Per-strike computed metrics."""
    strike: float
    distance: float                       # K - S
    abs_distance: float                   # |K - S|

    # Intrinsic / extrinsic
    call_intrinsic: float = 0.0
    call_extrinsic: float = 0.0
    put_intrinsic: float = 0.0
    put_extrinsic: float = 0.0

    # Decay / momentum
    call_decay_abs: Optional[float] = None
    call_decay_ratio: Optional[float] = None
    put_decay_abs: Optional[float] = None
    put_decay_ratio: Optional[float] = None

    # Liquidity
    call_volume: Optional[int] = None
    put_volume: Optional[int] = None
    liquidity_skew: Optional[float] = None

    # Spread quality
    call_spread: Optional[float] = None
    call_spread_pct: Optional[float] = None
    put_spread: Optional[float] = None
    put_spread_pct: Optional[float] = None

    # Curvature (near-ATM premium slope)
    call_slope: Optional[float] = None
    put_slope: Optional[float] = None

    # Extrinsic gradient (theta density)
    call_theta_density: Optional[float] = None
    put_theta_density: Optional[float] = None

    # Premium band
    call_ltp: Optional[float] = None
    put_ltp: Optional[float] = None
    call_band_eligible: bool = False
    put_band_eligible: bool = False

    # Scoring
    call_candidate_score: Optional[float] = None
    put_candidate_score: Optional[float] = None
    call_score_components: Optional[dict] = None
    put_score_components: Optional[dict] = None

    # Side bias flags
    side_bias: Optional[str] = None       # "CE" or "PE" or None


@dataclass(frozen=True)
class ExtrapolatedStrike:
    """A projected far-OTM strike not visible in the chain."""
    strike: float
    option_type: OptionType
    estimated_premium: float              # linear extrapolation
    adjusted_premium: float               # after compression (e^(-α·n))
    steps_from_atm: int
    alpha_used: float
    in_band: bool                         # falls within [Emin, Emax]
    score: Optional[float] = None
    score_components: Optional[dict] = None


@dataclass(frozen=True)
class CalculatedSnapshot:
    """All calculated metrics for one chain snapshot."""
    snapshot_id: str
    symbol: str
    spot_ltp: float
    config_version: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rows: tuple[CalculatedRow, ...] = ()
    extrapolated_ce: tuple[ExtrapolatedStrike, ...] = ()
    extrapolated_pe: tuple[ExtrapolatedStrike, ...] = ()

    # Aggregate bias
    avg_call_decay: Optional[float] = None
    avg_put_decay: Optional[float] = None
    bias_score: Optional[float] = None     # avg_call_decay - avg_put_decay
    preferred_side: Optional[Side] = None


# ── Analysis / Trigger Snapshot Models ─────────────────────────────────────

@dataclass(frozen=True)
class CandidateQuote:
    """Live quote for a shortlisted candidate strike."""
    strike: float
    option_type: OptionType
    ltp: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    volume: Optional[int] = None
    spread_pct: Optional[float] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AnalysisSnapshot:
    """Synchronized spot + chain snapshot used for calculations only.

    Created from a full REST chain refresh (every 30s).
    Spot and chain are from the SAME API call — no skew.
    Never mixed with WebSocket spot.
    """
    snapshot_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    symbol: str = ""
    expiry: str = ""
    spot_ltp: float = 0.0                  # spot from chain API (synchronized)
    chain: Optional['ChainSnapshot'] = None
    calculated: Optional[CalculatedSnapshot] = None
    quality: Optional['QualityReport'] = None
    best_ce: Optional[object] = None       # ScoredCandidate (avoiding circular import)
    best_pe: Optional[object] = None       # ScoredCandidate
    all_candidates: tuple = ()
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    config_version: str = ""

    @property
    def is_valid(self) -> bool:
        return self.chain is not None and self.spot_ltp > 0


@dataclass(frozen=True)
class TriggerSnapshot:
    """Live spot + candidate quotes for entry/exit decisions.

    Built every 1s from:
    - WebSocket spot (real-time)
    - REST quotes for 2-3 shortlisted candidate strikes
    - Latest 1-min candle status

    NOT used for full calculations (those use AnalysisSnapshot).
    """
    spot_ltp: float                        # from WebSocket (real-time)
    spot_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    candidate_quotes: tuple[CandidateQuote, ...] = ()
    candle_confirmed_above: Optional[float] = None   # trigger price if confirmed
    candle_confirmed_below: Optional[float] = None   # trigger price if confirmed
    candle_degraded: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def get_candidate_quote(self, strike: float, option_type: OptionType) -> Optional[CandidateQuote]:
        """Look up a specific candidate's live quote."""
        for q in self.candidate_quotes:
            if q.strike == strike and q.option_type == option_type:
                return q
        return None

    @property
    def has_candidates(self) -> bool:
        return len(self.candidate_quotes) > 0


# ── Quality Models ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class QualityCheck:
    """Result of a single data quality check."""
    check_name: str
    status: QualityStatus
    threshold: str                         # human-readable threshold
    observed: str                          # human-readable observed value
    result: bool                           # True = passed
    reason: str = ""


@dataclass(frozen=True)
class QualityReport:
    """Aggregate quality result for one snapshot."""
    snapshot_id: str
    symbol: str
    overall_status: QualityStatus
    quality_score: float                   # 0.0 - 1.0
    checks: tuple[QualityCheck, ...] = ()
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Signal Models ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalEvent:
    """A signal produced by the strategy engine per cycle."""
    signal_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    side_bias: Optional[Side] = None
    zone: str = ""                         # "CE_ACTIVE", "PE_ACTIVE", "NO_TRADE"
    machine_state: MachineState = MachineState.IDLE
    selected_strike: Optional[float] = None
    selected_option_type: Optional[OptionType] = None
    selected_premium: Optional[float] = None
    trigger_status: str = ""               # "TRIGGERED", "WAITING", "N/A"
    validity: SignalValidity = SignalValidity.INVALID
    rejection_reason: Optional[RejectionReason] = None
    rejection_detail: str = ""
    snapshot_id: str = ""
    config_version: str = ""
    spot_ltp: Optional[float] = None


# ── Trade Models ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PaperTrade:
    """A paper trade record."""
    trade_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp_entry: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timestamp_exit: Optional[datetime] = None
    side: Side = Side.PE
    symbol: str = ""
    expiry: str = ""
    strike: float = 0.0
    option_type: OptionType = OptionType.PE

    # Price roles — each captures a distinct lifecycle price
    selection_price: Optional[float] = None     # LTP when candidate was scored (AnalysisSnapshot)
    confirmation_price: Optional[float] = None  # LTP when confirmation passed (TriggerSnapshot)
    entry_price: float = 0.0                    # simulated fill price (after slippage)
    exit_price: Optional[float] = None          # simulated exit fill price
    qty: int = 0
    lots: int = 0
    capital_before: float = 0.0
    capital_after: Optional[float] = None
    sl: float = 0.0
    t1: float = 0.0
    t2: float = 0.0
    t3: float = 0.0
    pnl: Optional[float] = None
    charges: float = 0.0
    status: TradeStatus = TradeStatus.OPEN
    reason_entry: str = ""
    reason_exit: Optional[ExitReason] = None
    exit_detail: str = ""
    signal_id: str = ""
    snapshot_id: str = ""
    config_version: str = ""


@dataclass(frozen=True)
class CapitalLedgerEntry:
    """Single entry in the capital ledger."""
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    trade_id: Optional[str] = None
    event: str = ""                        # "TRADE_ENTRY", "TRADE_EXIT", "CHARGE", "INIT"
    amount: float = 0.0
    running_capital: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    daily_pnl: float = 0.0
    drawdown: float = 0.0
    peak_capital: float = 0.0


# ── Formula Audit Models ──────────────────────────────────────────────────

@dataclass(frozen=True)
class FormulaAudit:
    """Audit lineage for a single formula computation."""
    formula_name: str
    strike: Optional[float] = None
    option_type: Optional[OptionType] = None
    input_values: Optional[dict] = None
    intermediate_values: Optional[dict] = None
    output_value: Optional[float] = None
    config_version: str = ""
    rejection_reason: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class FormulaAuditBundle:
    """All formula audits for one cycle."""
    snapshot_id: str
    symbol: str
    config_version: str
    audits: tuple[FormulaAudit, ...] = ()
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Strike Rejection Audit ─────────────────────────────────────────────────

@dataclass(frozen=True)
class StrikeRejectionAudit:
    """Per-strike scan result — why a strike was accepted or rejected.

    One row per scanned strike per cycle. The main optimization dataset.
    """
    snapshot_id: str = ""
    symbol: str = ""
    strike: float = 0.0
    option_type: OptionType = OptionType.CE
    ltp: float = 0.0

    # Individual pass/fail flags
    band_pass: bool = False          # premium in [Emin, Emax]
    distance_pass: bool = False      # OTM distance >= min
    direction_pass: bool = False     # correct side of spot
    tradability_pass: bool = False   # bid/ask/spread/volume checks
    liquidity_pass: bool = False     # volume above threshold
    spread_pass: bool = False        # spread below threshold
    bias_pass: bool = False          # side bias alignment (optional)
    trigger_pass: bool = False       # spot crossed trigger for this side

    # Aggregate
    score: Optional[float] = None
    accepted: bool = False
    rejection_primary: Optional[str] = None
    rejection_all: tuple[str, ...] = ()

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "strike": self.strike,
            "option_type": self.option_type.value,
            "ltp": self.ltp,
            "band_pass": self.band_pass,
            "distance_pass": self.distance_pass,
            "direction_pass": self.direction_pass,
            "tradability_pass": self.tradability_pass,
            "liquidity_pass": self.liquidity_pass,
            "spread_pass": self.spread_pass,
            "bias_pass": self.bias_pass,
            "trigger_pass": self.trigger_pass,
            "score": self.score,
            "accepted": self.accepted,
            "rejection_primary": self.rejection_primary,
            "rejection_all": list(self.rejection_all),
        }


# ── Debug Models ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DebugTrace:
    """Stepwise debug trace for one calculation cycle."""
    cycle_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    snapshot_id: str = ""
    config_version: str = ""

    # Stepwise data
    fetch_summary: Optional[dict] = None
    validation_result: Optional[dict] = None
    derived_variables: Optional[dict] = None
    side_bias_decision: Optional[dict] = None
    strike_scan_results: Optional[dict] = None
    final_selection: Optional[dict] = None
    trade_decision: Optional[dict] = None
    paper_execution: Optional[dict] = None
    latency_ms: Optional[dict] = None


# ── Expiry Metadata ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExpiryInfo:
    """Expiry metadata for an instrument."""
    symbol: str
    expiry_date: str
    days_to_expiry: int
    expiry_type: str = ""                  # "WEEKLY" | "MONTHLY"
    is_holiday_adjusted: bool = False
