"""Config loader and settings dataclasses — all parameters config-driven.

Loads from settings.yaml, with environment variable overrides.
Every config instance gets a version hash for audit lineage.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml


# ── Enums ──────────────────────────────────────────────────────────────────

class ExpiryMode(Enum):
    NEAREST_WEEKLY = "NEAREST_WEEKLY"
    NEAREST_MONTHLY = "NEAREST_MONTHLY"
    SPECIFIC = "SPECIFIC"


class BandFitMode(Enum):
    BINARY = "BINARY"
    DISTANCE = "DISTANCE"


class TriggerMode(Enum):
    DYNAMIC = "DYNAMIC"
    STATIC = "STATIC"


class WindowType(Enum):
    FULL_CHAIN = "FULL_CHAIN"
    ATM_SYMMETRIC = "ATM_SYMMETRIC"
    VISIBLE_RANGE = "VISIBLE_RANGE"


class DecayMode(Enum):
    RAW = "RAW"
    NORMALIZED = "NORMALIZED"


class BiasAggregation(Enum):
    MEAN = "MEAN"
    VOLUME_WEIGHTED = "VOLUME_WEIGHTED"
    DISTANCE_WEIGHTED = "DISTANCE_WEIGHTED"


class AlphaMode(Enum):
    FIXED = "FIXED"
    CALIBRATED = "CALIBRATED"


class SnapshotMode(Enum):
    STRICT = "STRICT"
    TOLERANT = "TOLERANT"


class SizingMode(Enum):
    FIXED_LOTS = "FIXED_LOTS"
    FIXED_RUPEE = "FIXED_RUPEE"
    PCT_CAPITAL = "PCT_CAPITAL"
    PREMIUM_BUDGET = "PREMIUM_BUDGET"


class ExecutionMode(Enum):
    LTP = "LTP"
    MID = "MID"
    ASK = "ASK"
    BID = "BID"
    MID_SLIPPAGE = "MID_SLIPPAGE"


class LogLevel(Enum):
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


# ── Config Dataclasses ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class InstrumentConfig:
    symbol: str = "NIFTY"
    exchange: str = "NSE"
    strike_step: int = 50
    expiry_mode: ExpiryMode = ExpiryMode.NEAREST_WEEKLY


@dataclass(frozen=True)
class PremiumBandConfig:
    min: float = 2.10
    max: float = 8.50
    fit_mode: BandFitMode = BandFitMode.DISTANCE


@dataclass(frozen=True)
class OtmDistanceConfig:
    min_points: int = 250
    max_points: int = 450


@dataclass(frozen=True)
class TriggerConfig:
    mode: TriggerMode = TriggerMode.DYNAMIC
    upper_trigger: float = 22700.0
    lower_trigger: float = 22650.0


@dataclass(frozen=True)
class ScoringConfig:
    w1_distance: float = 1.0
    w2_momentum: float = 1.0
    w3_liquidity: float = 1.0
    w4_band_fit: float = 1.0
    w5_bias: float = 1.0
    tie_epsilon: float = 0.01
    min_valid_candidates: int = 1


@dataclass(frozen=True)
class WindowConfig:
    type: WindowType = WindowType.ATM_SYMMETRIC
    size: int = 4


@dataclass(frozen=True)
class DecayConfig:
    mode: DecayMode = DecayMode.NORMALIZED
    epsilon: float = 0.01


@dataclass(frozen=True)
class BiasConfig:
    aggregation: BiasAggregation = BiasAggregation.MEAN
    use_pcr: bool = False


@dataclass(frozen=True)
class ExtrapolationConfig:
    fit_window_ce: int = 3
    fit_window_pe: int = 3
    alpha_mode: AlphaMode = AlphaMode.CALIBRATED
    alpha_value: float = 0.05
    min_valid_strikes: int = 3


@dataclass(frozen=True)
class DataQualityConfig:
    max_spot_age_ms: int = 2000
    max_chain_age_ms: int = 5000
    max_cross_source_skew_ms: int = 3000
    intrinsic_floor_epsilon: float = 0.50
    min_volume: int = 1000
    min_oi: int = 500
    max_spread_pct: float = 5.0
    min_bid_qty: int = 1
    min_ask_qty: int = 1
    max_stale_cycles: int = 5
    snapshot_mode: SnapshotMode = SnapshotMode.STRICT


@dataclass(frozen=True)
class PaperTradingConfig:
    starting_capital: float = 100000.0
    lot_size: int = 75
    max_risk_per_trade_pct: float = 2.0
    max_daily_loss: float = 5000.0
    max_open_trades: int = 1
    max_daily_trades: int = 5
    max_consecutive_losses: int = 3
    sizing_mode: SizingMode = SizingMode.FIXED_LOTS
    fixed_lots: int = 1


@dataclass(frozen=True)
class ExecutionConfig:
    mode: ExecutionMode = ExecutionMode.MID_SLIPPAGE
    slippage_pct: float = 0.5
    brokerage_per_lot: float = 20.0
    exchange_charges_pct: float = 0.05


@dataclass(frozen=True)
class ExitRulesConfig:
    sl_ratio: float = 0.5
    t1_ratio: float = 2.0
    t2_ratio: float = 3.0
    t3_ratio: float = 4.0
    time_stop_minutes: int = 0
    eod_exit: bool = True
    trailing_stop: bool = False
    trailing_stop_pct: float = 0.0
    invalidation_exit: bool = True


@dataclass(frozen=True)
class TimeFiltersConfig:
    no_trade_first_minutes: int = 15
    no_trade_lunch_start: str = "12:30"
    no_trade_lunch_end: str = "13:15"
    mandatory_squareoff_time: str = "15:15"
    market_open: str = "09:15"
    market_close: str = "15:30"


@dataclass(frozen=True)
class CooldownConfig:
    seconds: int = 300
    max_reentries: int = 2
    allow_same_strike_reentry: bool = False


@dataclass(frozen=True)
class StateMachineConfig:
    no_trade_zone_enabled: bool = True


@dataclass(frozen=True)
class HysteresisConfig:
    buffer_points: float = 10.0
    min_zone_hold_seconds: float = 5.0
    rearm_distance_points: float = 20.0
    invalidation_buffer_points: float = 5.0


@dataclass(frozen=True)
class TradabilityConfig:
    require_bid: bool = True
    require_ask: bool = True
    min_bid_qty: int = 50
    min_ask_qty: int = 50
    min_recent_volume: int = 500
    max_spread_pct: float = 10.0
    max_last_trade_age_seconds: int = 0  # 0 = disabled


@dataclass(frozen=True)
class ConfirmationSettingsConfig:
    mode: str = "QUORUM"
    quorum: int = 2
    hold_duration_seconds: float = 15.0
    premium_expansion_min_pct: float = 5.0
    volume_spike_multiplier: float = 1.5
    spread_widen_max_pct: float = 20.0


@dataclass(frozen=True)
class StrategySettingsConfig:
    mode: str = "AUTO"


@dataclass(frozen=True)
class RefreshSettingsConfig:
    chain_idle_seconds: int = 30
    chain_active_seconds: int = 30
    candidate_zone_seconds: int = 5
    candidate_found_seconds: int = 2
    trade_quote_seconds: int = 1
    spot_drift_threshold: float = 100.0
    candidate_stale_seconds: float = 60.0


@dataclass(frozen=True)
class RiskConfig:
    cooldown_after_loss: bool = True
    no_trade_poor_quality: bool = True
    no_trade_near_close_minutes: int = 10


@dataclass(frozen=True)
class PollingConfig:
    interval_seconds: int = 1
    chain_refresh_seconds: int = 30
    retry_max_attempts: int = 3
    retry_backoff_base_ms: int = 500


@dataclass(frozen=True)
class LoggingConfig:
    level: LogLevel = LogLevel.INFO
    json_output: bool = True
    log_dir: str = "engines/lottery/logs"


@dataclass(frozen=True)
class StorageConfig:
    db_path: str = "engines/lottery/data/lottery.db"
    snapshot_dump_on_failure: bool = True
    snapshot_dump_on_signal: bool = True


@dataclass(frozen=True)
class AlertingConfig:
    enabled: bool = False
    channels: tuple = ()
    telegram_bot_token_env: str = "LOTTERY_TELEGRAM_TOKEN"
    telegram_chat_id_env: str = "LOTTERY_TELEGRAM_CHAT_ID"


# ── Root Config ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LotteryConfig:
    instrument: InstrumentConfig = field(default_factory=InstrumentConfig)
    premium_band: PremiumBandConfig = field(default_factory=PremiumBandConfig)
    otm_distance: OtmDistanceConfig = field(default_factory=OtmDistanceConfig)
    triggers: TriggerConfig = field(default_factory=TriggerConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    decay: DecayConfig = field(default_factory=DecayConfig)
    bias: BiasConfig = field(default_factory=BiasConfig)
    extrapolation: ExtrapolationConfig = field(default_factory=ExtrapolationConfig)
    data_quality: DataQualityConfig = field(default_factory=DataQualityConfig)
    paper_trading: PaperTradingConfig = field(default_factory=PaperTradingConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    exit_rules: ExitRulesConfig = field(default_factory=ExitRulesConfig)
    time_filters: TimeFiltersConfig = field(default_factory=TimeFiltersConfig)
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)
    state_machine: StateMachineConfig = field(default_factory=StateMachineConfig)
    hysteresis: HysteresisConfig = field(default_factory=HysteresisConfig)
    tradability: TradabilityConfig = field(default_factory=TradabilityConfig)
    confirmation: ConfirmationSettingsConfig = field(default_factory=ConfirmationSettingsConfig)
    strategy: StrategySettingsConfig = field(default_factory=StrategySettingsConfig)
    refresh: RefreshSettingsConfig = field(default_factory=RefreshSettingsConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    alerting: AlertingConfig = field(default_factory=AlertingConfig)

    @property
    def version_hash(self) -> str:
        """Deterministic hash of this config for audit lineage."""
        config_str = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(config_str.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        """Serialize to dict for storage/display."""
        return asdict(self)


# ── Loader Helpers ─────────────────────────────────────────────────────────

_ENUM_MAP = {
    "expiry_mode": ExpiryMode,
    "fit_mode": BandFitMode,
    "mode": None,  # handled per-section
    "type": WindowType,
    "aggregation": BiasAggregation,
    "alpha_mode": AlphaMode,
    "snapshot_mode": SnapshotMode,
    "sizing_mode": SizingMode,
    "level": LogLevel,
}

_SECTION_MODE_ENUMS = {
    "triggers": TriggerMode,
    "decay": DecayMode,
    "execution": ExecutionMode,
}


def _build_sub_config(cls, raw: dict, section_name: str = ""):
    """Build a frozen dataclass from a raw YAML dict, converting enums."""
    if raw is None:
        return cls()

    kwargs = {}
    for fld in cls.__dataclass_fields__:
        if fld not in raw:
            continue
        val = raw[fld]

        # Handle enum conversion
        if fld == "mode" and section_name in _SECTION_MODE_ENUMS:
            val = _SECTION_MODE_ENUMS[section_name](val)
        elif fld in _ENUM_MAP and _ENUM_MAP[fld] is not None:
            val = _ENUM_MAP[fld](val)
        elif fld == "channels" and isinstance(val, list):
            val = tuple(val)

        kwargs[fld] = val

    return cls(**kwargs)


def _apply_env_overrides(raw: dict) -> dict:
    """Apply environment variable overrides. Format: LOTTERY_SECTION_KEY=value."""
    for section_name, section_data in raw.items():
        if not isinstance(section_data, dict):
            continue
        for key in section_data:
            env_key = f"LOTTERY_{section_name.upper()}_{key.upper()}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                original = section_data[key]
                if isinstance(original, bool):
                    section_data[key] = env_val.lower() in ("true", "1", "yes")
                elif isinstance(original, int):
                    section_data[key] = int(env_val)
                elif isinstance(original, float):
                    section_data[key] = float(env_val)
                else:
                    section_data[key] = env_val
    return raw


# ── Public API ─────────────────────────────────────────────────────────────

_CONFIG_DIR = Path(__file__).parent


def load_config(path: Optional[str] = None) -> LotteryConfig:
    """Load config from YAML file with env overrides.

    Args:
        path: Path to YAML file. Defaults to settings.yaml in this directory.

    Returns:
        Frozen LotteryConfig instance with version hash.
    """
    config_path = Path(path) if path else _CONFIG_DIR / "settings.yaml"

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f) or {}

    raw = _apply_env_overrides(raw)

    return LotteryConfig(
        instrument=_build_sub_config(InstrumentConfig, raw.get("instrument"), "instrument"),
        premium_band=_build_sub_config(PremiumBandConfig, raw.get("premium_band"), "premium_band"),
        otm_distance=_build_sub_config(OtmDistanceConfig, raw.get("otm_distance"), "otm_distance"),
        triggers=_build_sub_config(TriggerConfig, raw.get("triggers"), "triggers"),
        scoring=_build_sub_config(ScoringConfig, raw.get("scoring"), "scoring"),
        window=_build_sub_config(WindowConfig, raw.get("window"), "window"),
        decay=_build_sub_config(DecayConfig, raw.get("decay"), "decay"),
        bias=_build_sub_config(BiasConfig, raw.get("bias"), "bias"),
        extrapolation=_build_sub_config(ExtrapolationConfig, raw.get("extrapolation"), "extrapolation"),
        data_quality=_build_sub_config(DataQualityConfig, raw.get("data_quality"), "data_quality"),
        paper_trading=_build_sub_config(PaperTradingConfig, raw.get("paper_trading"), "paper_trading"),
        execution=_build_sub_config(ExecutionConfig, raw.get("execution"), "execution"),
        exit_rules=_build_sub_config(ExitRulesConfig, raw.get("exit_rules"), "exit_rules"),
        time_filters=_build_sub_config(TimeFiltersConfig, raw.get("time_filters"), "time_filters"),
        cooldown=_build_sub_config(CooldownConfig, raw.get("cooldown"), "cooldown"),
        state_machine=_build_sub_config(StateMachineConfig, raw.get("state_machine"), "state_machine"),
        hysteresis=_build_sub_config(HysteresisConfig, raw.get("hysteresis"), "hysteresis"),
        tradability=_build_sub_config(TradabilityConfig, raw.get("tradability"), "tradability"),
        confirmation=_build_sub_config(ConfirmationSettingsConfig, raw.get("confirmation"), "confirmation"),
        strategy=_build_sub_config(StrategySettingsConfig, raw.get("strategy"), "strategy"),
        refresh=_build_sub_config(RefreshSettingsConfig, raw.get("refresh"), "refresh"),
        risk=_build_sub_config(RiskConfig, raw.get("risk"), "risk"),
        polling=_build_sub_config(PollingConfig, raw.get("polling"), "polling"),
        logging=_build_sub_config(LoggingConfig, raw.get("logging"), "logging"),
        storage=_build_sub_config(StorageConfig, raw.get("storage"), "storage"),
        alerting=_build_sub_config(AlertingConfig, raw.get("alerting"), "alerting"),
    )
