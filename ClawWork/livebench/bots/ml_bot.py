"""
Machine Learning Trading Bot (6th Bot)

This bot uses a trained ML model (XGBoost/RandomForest) to make trading decisions.
It automatically loads the model if it exists, otherwise operates in "shadow mode"
collecting data without making actual predictions.

Training Requirements:
- Minimum 500 completed trades with outcomes
- Run: python -m livebench.bots.ml_bot --train

Once trained, this bot participates in ensemble decisions automatically.
"""

import json
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import logging

from .base import TradingBot, BotSignal, SignalType, OptionType, TradeRecord
from .ml_features import MLFeatureExtractor, MLFeatures

logger = logging.getLogger(__name__)


@dataclass
class MLModelMetadata:
    """Metadata about the trained ML model"""
    model_type: str  # "xgboost", "random_forest", "gradient_boost"
    trained_at: str
    training_samples: int
    accuracy: float
    feature_importance: Dict[str, float]
    validation_metrics: Dict[str, float]


class MLTradingBot(TradingBot):
    """
    Machine Learning Trading Bot

    Uses trained ML model for predictions when available.
    Falls back to shadow mode (no predictions) when model not trained.

    Features:
    - Auto-loads model on startup if exists
    - Shadow mode for data collection before training
    - Confidence-based signal strength
    - Feature importance tracking
    """

    def __init__(self, data_dir: str = "data/bots"):
        super().__init__(
            name="MLPredictor",
            description="Machine learning model trained on historical patterns"
        )

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.model_path = self.data_dir / "ml_model.pkl"
        self.metadata_path = self.data_dir / "ml_model_metadata.json"
        self.scaler_path = self.data_dir / "ml_scaler.pkl"

        self.model = None
        self.scaler = None
        self.metadata: Optional[MLModelMetadata] = None
        self.feature_extractor = MLFeatureExtractor(str(self.data_dir))

        # Shadow mode tracking
        self.shadow_predictions: List[Dict] = []
        self.is_trained = False

        # Load model if exists
        self._load_model()

    def _load_model(self) -> bool:
        """Load trained model if exists"""
        if not self.model_path.exists():
            logger.info("ML model not found - running in shadow mode")
            return False

        try:
            with open(self.model_path, "rb") as f:
                self.model = pickle.load(f)

            if self.scaler_path.exists():
                with open(self.scaler_path, "rb") as f:
                    self.scaler = pickle.load(f)

            if self.metadata_path.exists():
                with open(self.metadata_path, "r") as f:
                    data = json.load(f)
                    self.metadata = MLModelMetadata(**data)

            self.is_trained = True
            logger.info(f"ML model loaded: {self.metadata.model_type if self.metadata else 'unknown'}")
            logger.info(f"Trained on {self.metadata.training_samples if self.metadata else '?'} samples")
            logger.info(f"Accuracy: {self.metadata.accuracy if self.metadata else '?'}%")
            return True

        except Exception as e:
            logger.error(f"Failed to load ML model: {e}")
            return False

    def analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        options_chain: List[Dict],
        bot_signals: Optional[List[BotSignal]] = None
    ) -> BotSignal:
        """
        Analyze market and generate ML-based signal

        If model not trained, returns HOLD with shadow prediction tracking.
        """
        # Extract features
        ml_features = self.feature_extractor.extract_features(
            index=index,
            market_data=market_data,
            options_chain=options_chain,
            bot_signals=bot_signals or []
        )

        # If not trained, shadow mode
        if not self.is_trained or self.model is None:
            return self._shadow_analyze(index, market_data, ml_features)

        # Make prediction
        try:
            prediction, confidence = self._predict(ml_features)

            signal_type = self._map_prediction_to_signal(prediction, confidence)
            option_type = self._determine_option_type(prediction, market_data)

            return BotSignal(
                bot_name=self.name,
                index=index,
                signal_type=signal_type,
                option_type=option_type,
                confidence=confidence,
                reasoning=self._generate_rationale(prediction, confidence, ml_features),
                entry=market_data.get("ltp", 0),
                stop_loss=self._calculate_stop_loss(market_data, prediction),
                target=self._calculate_target(market_data, prediction),
                factors={
                    "prediction": prediction,
                    "model_type": self.metadata.model_type if self.metadata else "unknown",
                    "top_features": self._get_top_features(ml_features),
                }
            )

        except Exception as e:
            logger.error(f"ML prediction failed: {e}")
            return BotSignal(
                bot_name=self.name,
                index=index,
                signal_type=SignalType.NEUTRAL,
                option_type=OptionType.NONE,
                confidence=0,
                reasoning=f"ML prediction error: {str(e)}"
            )

    def _shadow_analyze(
        self,
        index: str,
        market_data: Dict[str, Any],
        ml_features: MLFeatures
    ) -> BotSignal:
        """Shadow mode - track what we would predict for future validation"""
        # Store shadow prediction for later validation
        shadow = {
            "timestamp": datetime.now().isoformat(),
            "index": index,
            "ltp": market_data.get("ltp", 0),
            "features": ml_features.to_dict(),
        }
        self.shadow_predictions.append(shadow)

        # Keep only last 1000 shadow predictions
        if len(self.shadow_predictions) > 1000:
            self.shadow_predictions = self.shadow_predictions[-1000:]

        stats = self.feature_extractor.get_statistics()
        return BotSignal(
            bot_name=self.name,
            index=index,
            signal_type=SignalType.NEUTRAL,
            option_type=OptionType.NONE,
            confidence=0,
            reasoning="Shadow mode - collecting data for training",
            factors={
                "shadow_mode": True,
                "samples_collected": stats.get("total_records", 0),
                "completed_samples": stats.get("completed_trades", 0),
                "samples_needed": max(0, 100 - stats.get("completed_trades", 0)),
            }
        )

    def _predict(self, ml_features: MLFeatures) -> Tuple[int, float]:
        """
        Make prediction using ML model

        Returns:
            (prediction, confidence)
            prediction: 1 = profitable, 0 = loss, -1 = hold
            confidence: 0-100
        """
        # Convert features to array
        feature_vector = ml_features.to_array()

        # Scale if scaler exists
        if self.scaler is not None:
            feature_vector = self.scaler.transform([feature_vector])[0]

        # Predict
        prediction = self.model.predict([feature_vector])[0]

        # Get probability if available
        if hasattr(self.model, "predict_proba"):
            probas = self.model.predict_proba([feature_vector])[0]
            confidence = max(probas) * 100
        else:
            confidence = 60  # Default confidence

        return int(prediction), confidence

    def _map_prediction_to_signal(self, prediction: int, confidence: float) -> SignalType:
        """Map ML prediction to signal type"""
        if confidence < 55:
            return SignalType.NEUTRAL

        if prediction == 1:  # Profitable trade predicted
            return SignalType.STRONG_BUY if confidence >= 75 else SignalType.BUY
        elif prediction == 0:  # Loss predicted
            return SignalType.NEUTRAL  # Don't take predicted losses
        else:
            return SignalType.NEUTRAL

    def _determine_option_type(
        self,
        prediction: int,
        market_data: Dict[str, Any]
    ) -> Optional[OptionType]:
        """Determine CE or PE based on market bias"""
        if prediction != 1:
            return None

        change_pct = market_data.get("change_pct", 0)
        pcr = market_data.get("pcr", 1.0)

        # Use PCR and trend for direction
        if pcr > 1.1 or change_pct > 0.3:
            return OptionType.CE
        elif pcr < 0.9 or change_pct < -0.3:
            return OptionType.PE
        else:
            return OptionType.CE if change_pct >= 0 else OptionType.PE

    def _calculate_stop_loss(self, market_data: Dict[str, Any], prediction: int) -> float:
        """Calculate stop loss based on volatility"""
        ltp = market_data.get("ltp", 0)
        vix = market_data.get("vix", market_data.get("india_vix", 15))

        # Higher VIX = wider stop
        stop_pct = 0.3 + (vix / 100)  # 0.3% base + VIX adjustment

        return round(ltp * (1 - stop_pct / 100), 2)

    def _calculate_target(self, market_data: Dict[str, Any], prediction: int) -> float:
        """Calculate target based on prediction confidence"""
        ltp = market_data.get("ltp", 0)

        # 1:2 risk-reward ratio target
        target_pct = 0.6  # 0.6% target for 0.3% stop

        return round(ltp * (1 + target_pct / 100), 2)

    def _generate_rationale(
        self,
        prediction: int,
        confidence: float,
        ml_features: MLFeatures
    ) -> str:
        """Generate human-readable rationale"""
        if prediction == 1:
            action = "BUY"
        elif prediction == 0:
            action = "AVOID"
        else:
            action = "HOLD"

        # Top contributing features
        top_features = self._get_top_features(ml_features)
        features_str = ", ".join([f"{k}={v}" for k, v in list(top_features.items())[:3]])

        return f"ML predicts {action} ({confidence:.0f}% confidence). Key factors: {features_str}"

    def _get_top_features(self, ml_features: MLFeatures) -> Dict[str, Any]:
        """Get top contributing features for this prediction"""
        if not self.metadata or not self.metadata.feature_importance:
            return {}

        features = ml_features.to_dict()
        importance = self.metadata.feature_importance

        # Sort by importance
        sorted_features = sorted(
            [(k, v, importance.get(k, 0)) for k, v in features.items()],
            key=lambda x: x[2],
            reverse=True
        )

        return {k: v for k, v, _ in sorted_features[:5]}

    def get_status(self) -> Dict[str, Any]:
        """Get ML bot status"""
        stats = self.feature_extractor.get_statistics()
        total_samples = stats.get("total_records", 0)
        completed = stats.get("completed_trades", 0)

        return {
            "is_trained": self.is_trained,
            "model_type": self.metadata.model_type if self.metadata else None,
            "accuracy": self.metadata.accuracy if self.metadata else None,
            "training_samples": self.metadata.training_samples if self.metadata else 0,
            "current_samples": total_samples,
            "completed_samples": completed,
            "samples_needed": max(0, 100 - completed),
            "shadow_predictions": len(self.shadow_predictions),
            "ready_to_train": completed >= 100 and not self.is_trained,
        }

    def learn(self, trade: TradeRecord):
        """
        Learn from a completed trade.

        For ML bot, learning happens through:
        1. Feature extraction (already done at trade entry)
        2. Outcome recording (done by ensemble coordinator)
        3. Model retraining (manual step after enough data)

        This method updates internal tracking for potential future use.
        """
        # Track outcomes for shadow mode validation
        if not self.is_trained:
            # In shadow mode, we don't have predictions to validate
            return

        # Update performance tracking
        self.update_performance(trade)

        # Log learning event
        logger.info(
            f"ML bot learned from trade: {trade.trade_id} - "
            f"Outcome: {trade.outcome}, PnL: {trade.pnl_pct:.1f}%"
        )


def train_model(data_dir: str = "data/bots", model_type: str = "random_forest") -> bool:
    """
    Train ML model on collected data

    Args:
        data_dir: Directory containing ml_features.jsonl
        model_type: "random_forest", "xgboost", or "gradient_boost"

    Returns:
        True if training successful
    """
    import numpy as np

    try:
        from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
        from sklearn.model_selection import train_test_split, cross_val_score
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import accuracy_score, classification_report
    except ImportError:
        print("Please install scikit-learn: pip install scikit-learn")
        return False

    data_path = Path(data_dir)
    feature_file = data_path / "ml_features.jsonl"

    if not feature_file.exists():
        print(f"Feature file not found: {feature_file}")
        return False

    # Load data
    print("Loading training data...")
    samples = []
    with open(feature_file, "r") as f:
        for line in f:
            try:
                sample = json.loads(line.strip())
                if sample.get("outcome") is not None:
                    samples.append(sample)
            except json.JSONDecodeError:
                continue

    if len(samples) < 100:
        print(f"Not enough samples: {len(samples)} (need at least 100)")
        return False

    print(f"Loaded {len(samples)} samples with outcomes")

    # Prepare features and labels
    feature_names = [
        "change_pct", "range_pct", "body_pct", "gap_pct",
        "vix_normalized", "pcr", "oi_ratio",
        "ce_oi_change_pct", "pe_oi_change_pct",
        "max_pain_distance", "atm_iv",
        "time_bucket", "days_to_expiry_bucket", "regime_encoded",
        "consensus_strength", "signal_agreement",
    ]

    X = []
    y = []

    for sample in samples:
        features = sample.get("features", {})
        outcome = sample.get("outcome")

        # Build feature vector
        vector = []
        for name in feature_names:
            value = features.get(name, 0)
            if isinstance(value, (int, float)):
                vector.append(value)
            else:
                vector.append(0)

        X.append(vector)
        y.append(1 if outcome == "profit" else 0)

    X = np.array(X)
    y = np.array(y)

    print(f"Feature matrix shape: {X.shape}")
    print(f"Profit samples: {sum(y)}, Loss samples: {len(y) - sum(y)}")

    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Select model
    print(f"\nTraining {model_type} model...")

    if model_type == "xgboost":
        try:
            from xgboost import XGBClassifier
            model = XGBClassifier(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                random_state=42
            )
        except ImportError:
            print("XGBoost not installed, falling back to RandomForest")
            model_type = "random_forest"
            model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                random_state=42
            )
    elif model_type == "gradient_boost":
        model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            random_state=42
        )
    else:
        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            random_state=42
        )

    # Train
    model.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred = model.predict(X_test_scaled)
    accuracy = accuracy_score(y_test, y_pred) * 100

    print(f"\n=== Model Performance ===")
    print(f"Accuracy: {accuracy:.1f}%")
    print(f"\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=["Loss", "Profit"]))

    # Cross-validation
    cv_scores = cross_val_score(model, X_train_scaled, y_train, cv=5)
    print(f"Cross-validation: {cv_scores.mean()*100:.1f}% (+/- {cv_scores.std()*100:.1f}%)")

    # Feature importance
    if hasattr(model, "feature_importances_"):
        importance = dict(zip(feature_names, model.feature_importances_))
        sorted_importance = sorted(importance.items(), key=lambda x: x[1], reverse=True)

        print(f"\n=== Top 10 Important Features ===")
        for name, imp in sorted_importance[:10]:
            print(f"  {name}: {imp:.4f}")
    else:
        importance = {}

    # Save model
    print("\nSaving model...")

    with open(data_path / "ml_model.pkl", "wb") as f:
        pickle.dump(model, f)

    with open(data_path / "ml_scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)

    metadata = {
        "model_type": model_type,
        "trained_at": datetime.now().isoformat(),
        "training_samples": len(samples),
        "accuracy": round(accuracy, 2),
        "feature_importance": {k: float(v) for k, v in importance.items()},
        "validation_metrics": {
            "test_accuracy": round(accuracy, 2),
            "cv_mean": round(cv_scores.mean() * 100, 2),
            "cv_std": round(cv_scores.std() * 100, 2),
        }
    }

    with open(data_path / "ml_model_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nModel saved to {data_path}")
    print("The ML bot will automatically use this model on next startup.")

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ML Trading Bot Training")
    parser.add_argument("--train", action="store_true", help="Train ML model")
    parser.add_argument("--data-dir", default="data/bots", help="Data directory")
    parser.add_argument(
        "--model-type",
        choices=["random_forest", "xgboost", "gradient_boost"],
        default="random_forest",
        help="Model type to train"
    )
    parser.add_argument("--status", action="store_true", help="Show ML bot status")

    args = parser.parse_args()

    if args.train:
        success = train_model(args.data_dir, args.model_type)
        exit(0 if success else 1)
    elif args.status:
        bot = MLTradingBot(args.data_dir)
        status = bot.get_status()
        print("\n=== ML Bot Status ===")
        for key, value in status.items():
            print(f"  {key}: {value}")
    else:
        parser.print_help()
