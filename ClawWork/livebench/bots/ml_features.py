"""
ML Feature Extractor - Prepares Universal Features for Machine Learning

Creates normalized, universal features that work across ALL indices:
- All features are ratios or percentages (not absolute values)
- Features capture market structure, not specific price levels
- Ready for XGBoost, RandomForest, or Neural Network training

Usage:
    extractor = MLFeatureExtractor()
    features = extractor.extract_features(market_data)

    # For training
    X_train, y_train = extractor.prepare_training_data()

    # Export for external ML tools
    extractor.export_to_csv("training_data.csv")
"""

import json
import csv
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import os

DEFAULT_BOT_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "bots"


@dataclass
class MLFeatures:
    """
    Universal features that work across all indices

    All features are normalized (ratios, percentages, buckets)
    so they can be applied to any stock/index
    """
    # Identification (not used in training)
    timestamp: str
    index: str
    trade_id: str

    # Price Action Features (universal)
    change_pct: float          # % change from open
    range_pct: float           # (high-low)/ltp * 100
    body_pct: float            # |close-open|/ltp * 100
    position_in_range: float   # Where is price in day's range (0-1)

    # Options Features (universal ratios)
    pcr: float                 # Put-Call Ratio
    pcr_bucket: int            # 0=very_low, 1=low, 2=neutral, 3=high, 4=very_high
    ce_oi_change_pct: float    # % change in CE OI
    pe_oi_change_pct: float    # % change in PE OI
    oi_change_ratio: float     # PE OI change / CE OI change

    # OI Pattern (categorical)
    oi_pattern: int            # 0=bullish_buildup, 1=short_covering, 2=bearish_buildup, 3=long_unwinding

    # Volatility Features (universal)
    iv_percentile: float       # 0-100
    iv_bucket: int             # 0=very_low, 1=low, 2=normal, 3=high, 4=very_high
    vix: float                 # India VIX
    vix_bucket: int            # 0=complacent, 1=low, 2=normal, 3=elevated, 4=fear

    # Time Features (universal)
    hour: int                  # 9-15
    minute_bucket: int         # 0=first_15, 1=morning, 2=midday, 3=afternoon, 4=last_30
    day_of_week: int           # 0=Monday, 4=Friday
    is_expiry_day: int         # 0 or 1
    is_monthly_expiry: int     # 0 or 1
    days_to_expiry: int        # 0-7

    # Regime Features (universal)
    regime: int                # 0=trending_up, 1=trending_down, 2=ranging, 3=high_vol, 4=breakout
    trend_strength: float      # 0-100
    regime_bias: int           # 0=bearish, 1=neutral, 2=bullish

    # Max Pain Features (universal)
    distance_to_max_pain_pct: float  # % distance from max pain
    price_vs_max_pain: int     # -1=below, 0=at, 1=above

    # Signal Features
    num_bullish_bots: int      # 0-5
    num_bearish_bots: int      # 0-5
    consensus_pct: float       # 0-100
    avg_bot_confidence: float  # 0-100

    # Action taken
    action: int                # 0=no_trade, 1=buy_ce, 2=buy_pe

    # Target (for training) - filled after trade closes
    outcome: int = -1          # -1=pending, 0=loss, 1=breakeven, 2=win
    pnl_pct: float = 0.0       # % P&L

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return asdict(self)


class MLFeatureExtractor:
    """
    Extracts universal ML features from market data

    Key principle: All features must be index-agnostic
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.getenv(
            "BOT_DATA_DIR",
            str(DEFAULT_BOT_DATA_DIR)
        ))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.features_file = self.data_dir / "ml_features.jsonl"
        self.training_file = self.data_dir / "ml_training_data.csv"

    def extract_features(
        self,
        market_data: Dict[str, Any],
        bot_signals: Any = None,
        action: str = "NO_TRADE",
        trade_id: str = "",
        index: str = "",
        options_chain: List = None
    ) -> MLFeatures:
        """Extract universal features from market data"""

        ltp = market_data.get("ltp", 0)
        open_price = market_data.get("open", ltp)
        high = market_data.get("high", ltp)
        low = market_data.get("low", ltp)

        # Price action features
        change_pct = market_data.get("change_pct", 0)
        range_pct = ((high - low) / ltp * 100) if ltp > 0 else 0
        body_pct = (abs(ltp - open_price) / ltp * 100) if ltp > 0 else 0
        position_in_range = ((ltp - low) / (high - low)) if (high - low) > 0 else 0.5

        # PCR features
        pcr = market_data.get("pcr", 1.0)
        pcr_bucket = self._bucket_pcr(pcr)

        # OI features
        ce_oi = market_data.get("ce_oi", 1)
        pe_oi = market_data.get("pe_oi", 1)
        ce_oi_change = market_data.get("ce_oi_change", 0)
        pe_oi_change = market_data.get("pe_oi_change", 0)

        ce_oi_change_pct = (ce_oi_change / ce_oi * 100) if ce_oi > 0 else 0
        pe_oi_change_pct = (pe_oi_change / pe_oi * 100) if pe_oi > 0 else 0
        oi_change_ratio = (pe_oi_change / ce_oi_change) if ce_oi_change != 0 else 1.0

        # OI pattern
        oi_pattern = self._get_oi_pattern(change_pct, ce_oi_change + pe_oi_change > 0)

        # Volatility features
        iv_percentile = market_data.get("iv_percentile", 50)
        iv_bucket = self._bucket_iv(iv_percentile)
        vix = market_data.get("vix", market_data.get("india_vix", 15))
        vix_bucket = self._bucket_vix(vix)

        # Time features
        now = datetime.now()
        hour = now.hour
        minute_bucket = self._bucket_minute(now.minute, now.hour)
        day_of_week = now.weekday()

        day_type = market_data.get("day_type", "NORMAL")
        is_expiry = 1 if "EXPIRY" in day_type else 0
        is_monthly = 1 if "MONTHLY" in day_type else 0
        days_to_expiry = market_data.get("days_to_expiry", 7)

        # Regime features
        regime_str = market_data.get("market_regime", "UNKNOWN")
        regime = self._encode_regime(regime_str)
        trend_strength = market_data.get("trend_strength", 50)
        bias_str = market_data.get("regime_bias", "NEUTRAL")
        regime_bias = {"BEARISH": 0, "NEUTRAL": 1, "BULLISH": 2}.get(bias_str, 1)

        # Max pain features
        max_pain = market_data.get("max_pain", ltp)
        distance_to_max_pain = ((ltp - max_pain) / ltp * 100) if ltp > 0 else 0
        price_vs_max_pain = 1 if ltp > max_pain else (-1 if ltp < max_pain else 0)

        # Bot signal features
        bot_signals = bot_signals or market_data.get("_bot_signals", {})
        num_bullish = 0
        num_bearish = 0
        confidences = []

        # Handle both list and dict formats for bot_signals
        if isinstance(bot_signals, list):
            # List of BotSignal objects or dicts
            for signal in bot_signals:
                if hasattr(signal, 'signal_type'):
                    # BotSignal object
                    sig_type = str(signal.signal_type.value) if hasattr(signal.signal_type, 'value') else str(signal.signal_type)
                    conf = signal.confidence
                else:
                    # Dict
                    sig_type = signal.get("signal_type", "")
                    conf = signal.get("confidence", 50)
                confidences.append(conf)
                if "BUY" in sig_type.upper():
                    num_bullish += 1
                elif "SELL" in sig_type.upper():
                    num_bearish += 1
        elif isinstance(bot_signals, dict):
            for bot_name, signal in bot_signals.items():
                sig_type = signal.get("signal_type", "") if isinstance(signal, dict) else ""
                conf = signal.get("confidence", 50) if isinstance(signal, dict) else 50
                confidences.append(conf)
                if "BUY" in str(sig_type).upper():
                    num_bullish += 1
                elif "SELL" in str(sig_type).upper():
                    num_bearish += 1

        total_bots = max(num_bullish + num_bearish, 1)
        consensus_pct = max(num_bullish, num_bearish) / 5 * 100  # Assuming 5 bots
        avg_confidence = sum(confidences) / len(confidences) if confidences else 50

        # Action encoding
        action_code = {"NO_TRADE": 0, "BUY_CE": 1, "BUY_PE": 2}.get(action, 0)

        return MLFeatures(
            timestamp=now.isoformat(),
            index=market_data.get("index", "UNKNOWN"),
            trade_id=trade_id,
            change_pct=round(change_pct, 4),
            range_pct=round(range_pct, 4),
            body_pct=round(body_pct, 4),
            position_in_range=round(position_in_range, 4),
            pcr=round(pcr, 4),
            pcr_bucket=pcr_bucket,
            ce_oi_change_pct=round(ce_oi_change_pct, 4),
            pe_oi_change_pct=round(pe_oi_change_pct, 4),
            oi_change_ratio=round(min(10, max(-10, oi_change_ratio)), 4),
            oi_pattern=oi_pattern,
            iv_percentile=round(iv_percentile, 2),
            iv_bucket=iv_bucket,
            vix=round(vix, 2),
            vix_bucket=vix_bucket,
            hour=hour,
            minute_bucket=minute_bucket,
            day_of_week=day_of_week,
            is_expiry_day=is_expiry,
            is_monthly_expiry=is_monthly,
            days_to_expiry=days_to_expiry,
            regime=regime,
            trend_strength=round(trend_strength, 2),
            regime_bias=regime_bias,
            distance_to_max_pain_pct=round(distance_to_max_pain, 4),
            price_vs_max_pain=price_vs_max_pain,
            num_bullish_bots=num_bullish,
            num_bearish_bots=num_bearish,
            consensus_pct=round(consensus_pct, 2),
            avg_bot_confidence=round(avg_confidence, 2),
            action=action_code,
        )

    def _bucket_pcr(self, pcr: float) -> int:
        """Bucket PCR into categories"""
        if pcr <= 0.6:
            return 0  # very_low (bearish)
        elif pcr <= 0.85:
            return 1  # low
        elif pcr <= 1.15:
            return 2  # neutral
        elif pcr <= 1.4:
            return 3  # high
        else:
            return 4  # very_high (bullish)

    def _bucket_iv(self, iv_percentile: float) -> int:
        """Bucket IV percentile"""
        if iv_percentile <= 20:
            return 0
        elif iv_percentile <= 40:
            return 1
        elif iv_percentile <= 60:
            return 2
        elif iv_percentile <= 80:
            return 3
        else:
            return 4

    def _bucket_vix(self, vix: float) -> int:
        """Bucket VIX into fear/greed levels"""
        if vix <= 12:
            return 0  # complacent
        elif vix <= 15:
            return 1  # low
        elif vix <= 18:
            return 2  # normal
        elif vix <= 22:
            return 3  # elevated
        else:
            return 4  # fear

    def _bucket_minute(self, minute: int, hour: int) -> int:
        """Bucket time into trading periods"""
        total_minutes = hour * 60 + minute

        if total_minutes < 9 * 60 + 30:  # Before 9:30
            return 0  # first_15
        elif total_minutes < 10 * 60:  # Before 10:00
            return 0  # first_15
        elif total_minutes < 12 * 60:  # Before 12:00
            return 1  # morning
        elif total_minutes < 14 * 60:  # Before 14:00
            return 2  # midday
        elif total_minutes < 15 * 60:  # Before 15:00
            return 3  # afternoon
        else:
            return 4  # last_30

    def _get_oi_pattern(self, change_pct: float, oi_increasing: bool) -> int:
        """Determine OI pattern"""
        price_up = change_pct > 0.1

        if price_up and oi_increasing:
            return 0  # bullish_buildup
        elif price_up and not oi_increasing:
            return 1  # short_covering
        elif not price_up and oi_increasing:
            return 2  # bearish_buildup
        else:
            return 3  # long_unwinding

    def _encode_regime(self, regime: str) -> int:
        """Encode regime to integer"""
        regimes = {
            "TRENDING_UP": 0,
            "TRENDING_DOWN": 1,
            "RANGING": 2,
            "HIGH_VOLATILITY": 3,
            "BREAKOUT_UP": 4,
            "BREAKOUT_DOWN": 5,
        }
        return regimes.get(regime, 2)  # Default to ranging

    def record_features(self, features: MLFeatures):
        """Record features to disk"""
        with open(self.features_file, "a") as f:
            f.write(json.dumps(asdict(features)) + "\n")

    def update_outcome(self, trade_id: str, outcome: str, pnl_pct: float):
        """Update outcome for a recorded trade"""
        outcome_code = {"LOSS": 0, "BREAKEVEN": 1, "WIN": 2}.get(outcome, -1)

        # Read all features
        features = []
        if self.features_file.exists():
            with open(self.features_file, "r") as f:
                for line in f:
                    if line.strip():
                        features.append(json.loads(line))

        # Update matching trade
        for feat in features:
            if feat.get("trade_id") == trade_id:
                feat["outcome"] = outcome_code
                feat["pnl_pct"] = pnl_pct
                break

        # Rewrite file
        with open(self.features_file, "w") as f:
            for feat in features:
                f.write(json.dumps(feat) + "\n")

    def get_training_data(self) -> Tuple[List[List[float]], List[int]]:
        """
        Get training data as X (features) and y (labels)

        Returns:
            X: List of feature vectors
            y: List of outcome labels (0=loss, 1=breakeven, 2=win)
        """
        X = []
        y = []

        if not self.features_file.exists():
            return X, y

        # Feature columns to use (excluding non-features)
        feature_columns = [
            "change_pct", "range_pct", "body_pct", "position_in_range",
            "pcr", "pcr_bucket", "ce_oi_change_pct", "pe_oi_change_pct",
            "oi_change_ratio", "oi_pattern", "iv_percentile", "iv_bucket",
            "vix", "vix_bucket", "hour", "minute_bucket", "day_of_week",
            "is_expiry_day", "is_monthly_expiry", "days_to_expiry",
            "regime", "trend_strength", "regime_bias",
            "distance_to_max_pain_pct", "price_vs_max_pain",
            "num_bullish_bots", "num_bearish_bots", "consensus_pct",
            "avg_bot_confidence", "action"
        ]

        with open(self.features_file, "r") as f:
            for line in f:
                if not line.strip():
                    continue

                feat = json.loads(line)

                # Only include completed trades
                if feat.get("outcome", -1) == -1:
                    continue

                # Extract feature vector
                feature_vector = [feat.get(col, 0) for col in feature_columns]
                X.append(feature_vector)
                y.append(feat["outcome"])

        return X, y

    def export_to_csv(self, filepath: str = None):
        """Export training data to CSV for external ML tools"""
        path = Path(filepath or self.training_file)

        if not self.features_file.exists():
            return

        # Read all features
        all_features = []
        with open(self.features_file, "r") as f:
            for line in f:
                if line.strip():
                    all_features.append(json.loads(line))

        if not all_features:
            return

        # Write CSV
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_features[0].keys())
            writer.writeheader()
            writer.writerows(all_features)

        print(f"Exported {len(all_features)} records to {path}")

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about collected data"""
        if not self.features_file.exists():
            return {"total_records": 0}

        total = 0
        completed = 0
        wins = 0
        losses = 0
        by_index = {}
        by_regime = {}
        by_pcr_bucket = {}

        with open(self.features_file, "r") as f:
            for line in f:
                if not line.strip():
                    continue

                feat = json.loads(line)
                total += 1

                index = feat.get("index", "UNKNOWN")
                by_index[index] = by_index.get(index, 0) + 1

                regime = feat.get("regime", 2)
                by_regime[regime] = by_regime.get(regime, 0) + 1

                pcr_bucket = feat.get("pcr_bucket", 2)
                by_pcr_bucket[pcr_bucket] = by_pcr_bucket.get(pcr_bucket, 0) + 1

                outcome = feat.get("outcome", -1)
                if outcome >= 0:
                    completed += 1
                    if outcome == 2:
                        wins += 1
                    elif outcome == 0:
                        losses += 1

        return {
            "total_records": total,
            "completed_trades": completed,
            "pending_trades": total - completed,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / completed * 100) if completed > 0 else 0,
            "by_index": by_index,
            "by_regime": by_regime,
            "by_pcr_bucket": by_pcr_bucket,
            "ready_for_ml": completed >= 100,
            "ml_confidence": "high" if completed >= 500 else ("medium" if completed >= 200 else "low"),
        }
