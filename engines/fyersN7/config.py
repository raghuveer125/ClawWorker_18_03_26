"""
FyersN7 engine configuration — typed Python dataclass.

Replaces 40+ bash env-var defaults previously scattered in run_paper_trade_loop.sh
and other shell scripts.  Each field can still be overridden by environment variables.

Usage:
    cfg = FyersN7Config.from_env()
    print(cfg.capital, cfg.lot_size, cfg.min_confidence)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(key: str, default: str, env: dict[str, str] | None = None) -> str:
    return (env or os.environ).get(key, default)


def _env_int(key: str, default: int, env: dict[str, str] | None = None) -> int:
    return int(_env(key, str(default), env))


def _env_float(key: str, default: float, env: dict[str, str] | None = None) -> float:
    return float(_env(key, str(default), env))


def _env_bool(key: str, default: bool, env: dict[str, str] | None = None) -> bool:
    val = _env(key, "1" if default else "0", env)
    return val.lower() in ("1", "true", "yes")


@dataclass
class FyersN7Config:
    """Complete configuration for the FyersN7 paper/live trading engine."""

    # ── Polling & timing ─────────────────────────────────────────────────────
    interval_sec: int = 15
    max_hold_sec: int = 180
    flip_cooldown_sec: int = 45
    reentry_cooldown_sec: int = 300

    # ── Capital & sizing ─────────────────────────────────────────────────────
    capital: float = 100_000.0
    lot_size: int = 10
    entry_fee: float = 40.0
    exit_fee: float = 40.0
    risk_per_trade_pct: float = 0.0
    max_lot_multiplier: float = 3.0

    # ── Risk limits ──────────────────────────────────────────────────────────
    daily_loss_limit: float = 0.0       # 0 = unlimited
    max_trades_per_day: int = 0         # 0 = unlimited
    max_concurrent_positions: int = 0   # 0 = unlimited

    # ── Signal filters ───────────────────────────────────────────────────────
    exit_target: str = "t1"
    profile: str = "expiry"
    ladder_count: int = 5
    otm_start: int = 1
    max_premium: float = 1200.0
    min_premium: float = 0.0
    min_confidence: int = 88
    min_score: int = 95
    min_abs_delta: float = 0.10
    min_vote_diff: int = 2
    confirm_pulls: int = 2
    max_select_strikes: int = 3
    max_spread_pct: float = 2.5

    # ── Adaptive model ───────────────────────────────────────────────────────
    adaptive_enable: bool = True
    adaptive_model_file: str = ".adaptive_model.json"
    min_learn_prob: float = 0.55
    min_model_samples: int = 20
    hard_gate_min_model_samples: int = 100
    learn_gate_lock_streak: int = 8
    learn_gate_relax_sec: int = 300
    train_min_labels: int = 20
    train_lr: float = 0.15
    train_epochs: int = 600
    auto_train_on_backfill: bool = True

    # ── Display ──────────────────────────────────────────────────────────────
    show_signal_table: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "FyersN7Config":
        """Load config from environment variables, falling back to defaults."""
        e = env or dict(os.environ)
        min_model = _env_int("MIN_MODEL_SAMPLES", 20, e)
        return cls(
            interval_sec=_env_int("INTERVAL_SEC", 15, e),
            max_hold_sec=_env_int("MAX_HOLD_SEC", 180, e),
            flip_cooldown_sec=_env_int("FLIP_COOLDOWN_SEC", 45, e),
            reentry_cooldown_sec=_env_int("REENTRY_COOLDOWN_SEC", 300, e),
            capital=_env_float("CAPITAL", 100_000.0, e),
            lot_size=_env_int("LOT_SIZE", 10, e),
            entry_fee=_env_float("ENTRY_FEE", 40.0, e),
            exit_fee=_env_float("EXIT_FEE", 40.0, e),
            risk_per_trade_pct=_env_float("RISK_PER_TRADE_PCT", 0.0, e),
            max_lot_multiplier=_env_float("MAX_LOT_MULTIPLIER", 3.0, e),
            daily_loss_limit=_env_float("DAILY_LOSS_LIMIT", 0.0, e),
            max_trades_per_day=_env_int("MAX_TRADES_PER_DAY", 0, e),
            max_concurrent_positions=_env_int("MAX_CONCURRENT_POSITIONS", 0, e),
            exit_target=_env("EXIT_TARGET", "t1", e),
            profile=_env("PROFILE", "expiry", e),
            ladder_count=_env_int("LADDER_COUNT", 5, e),
            otm_start=_env_int("OTM_START", 1, e),
            max_premium=_env_float("MAX_PREMIUM", 1200.0, e),
            min_premium=_env_float("MIN_PREMIUM", 0.0, e),
            min_confidence=_env_int("MIN_CONFIDENCE", 88, e),
            min_score=_env_int("MIN_SCORE", 95, e),
            min_abs_delta=_env_float("MIN_ABS_DELTA", 0.10, e),
            min_vote_diff=_env_int("MIN_VOTE_DIFF", 2, e),
            confirm_pulls=_env_int("CONFIRM_PULLS", 2, e),
            max_select_strikes=_env_int("MAX_SELECT_STRIKES", 3, e),
            max_spread_pct=_env_float("MAX_SPREAD_PCT", 2.5, e),
            adaptive_enable=_env_bool("ADAPTIVE_ENABLE", True, e),
            adaptive_model_file=_env("ADAPTIVE_MODEL_FILE", ".adaptive_model.json", e),
            min_learn_prob=_env_float("MIN_LEARN_PROB", 0.55, e),
            min_model_samples=min_model,
            hard_gate_min_model_samples=_env_int("HARD_GATE_MIN_MODEL_SAMPLES", 100, e),
            learn_gate_lock_streak=_env_int("LEARN_GATE_LOCK_STREAK", 8, e),
            learn_gate_relax_sec=_env_int("LEARN_GATE_RELAX_SEC", 300, e),
            train_min_labels=_env_int("TRAIN_MIN_LABELS", min_model, e),
            train_lr=_env_float("TRAIN_LR", 0.15, e),
            train_epochs=_env_int("TRAIN_EPOCHS", 600, e),
            auto_train_on_backfill=_env_bool("AUTO_TRAIN_ON_BACKFILL", True, e),
            show_signal_table=_env_bool("SHOW_SIGNAL_TABLE", False, e),
        )
