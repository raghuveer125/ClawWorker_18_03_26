"""
Dealer pressure and gamma regime inference from option chain structure.
"""

from collections import defaultdict
from statistics import mean
from typing import Any, DefaultDict, Dict, Union

from ..base import BaseBot, BotContext, BotResult, BotStatus
from ..config import ScalpingConfig


class DealerPressureAgent(BaseBot):
    BOT_TYPE = "dealer_pressure"
    REQUIRES_LLM = False

    def get_description(self) -> str:
        return "Infers dealer gamma regime, gamma flip level, and pinning/acceleration pressure"

    async def execute(self, context: BotContext) -> BotResult:
        option_chains = context.data.get("option_chains", {})
        spot_data = context.data.get("spot_data", {})
        config = context.data.get("config", ScalpingConfig())

        dealer_pressure: Dict[str, Dict[str, Union[float, str]]] = {}
        for symbol, chain in option_chains.items():
            spot = spot_data.get(symbol)
            spot_price = float(getattr(spot, "ltp", getattr(chain, "spot_price", 0)) or 0)
            dealer_pressure[symbol] = self._analyze_symbol(chain, spot_price, config)

        context.data["dealer_pressure"] = dealer_pressure

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={"symbols": dealer_pressure},
            metrics={"symbols_analyzed": len(dealer_pressure)},
        )

    def _analyze_symbol(
        self,
        chain: Any,
        spot_price: float,
        config: ScalpingConfig,
    ) -> Dict[str, Union[float, str]]:
        by_strike: DefaultDict[int, Dict[str, float]] = defaultdict(lambda: {"oi": 0.0, "gamma": 0.0, "ce_oi": 0.0, "pe_oi": 0.0})
        max_oi_strike = 0
        max_oi = -1.0

        for opt in getattr(chain, "options", []):
            strike = int(getattr(opt, "strike", 0) or 0)
            oi = float(getattr(opt, "oi", 0) or 0)
            gamma = abs(float(getattr(opt, "gamma", 0) or 0))
            option_type = str(getattr(opt, "option_type", "")).upper()
            row = by_strike[strike]
            row["oi"] += oi
            row["gamma"] += gamma * oi
            if option_type == "CE":
                row["ce_oi"] += oi
            elif option_type == "PE":
                row["pe_oi"] += oi
            if row["oi"] > max_oi:
                max_oi = row["oi"]
                max_oi_strike = strike

        if not by_strike:
            return {
                "gamma_regime": "neutral",
                "gamma_flip_level": spot_price,
                "pinning_score": 0.0,
                "acceleration_score": 0.0,
                "max_oi_strike": spot_price,
            }

        gamma_flip_level = self._estimate_gamma_flip(by_strike, spot_price)
        pin_distance_pct = abs(spot_price - max_oi_strike) / max(abs(spot_price), 1.0) * 100
        pinning_score = max(0.0, 1.0 - (pin_distance_pct / max(config.dealer_pin_proximity_pct, 0.01)))
        gamma_density = self._gamma_density(by_strike)
        acceleration_score = min(1.0, gamma_density * max(pin_distance_pct / max(config.dealer_pin_proximity_pct, 0.01), 0.0))

        gamma_regime = "neutral"
        if pinning_score >= 0.65 and gamma_density >= 0.35:
            gamma_regime = "long"
        elif acceleration_score >= 0.70:
            gamma_regime = "short"

        return {
            "gamma_regime": gamma_regime,
            "gamma_flip_level": float(gamma_flip_level),
            "pinning_score": round(pinning_score, 4),
            "acceleration_score": round(min(acceleration_score, 1.0), 4),
            "max_oi_strike": float(max_oi_strike),
            "gamma_density": round(gamma_density, 4),
        }

    def _estimate_gamma_flip(self, by_strike: Dict[int, Dict[str, float]], spot_price: float) -> float:
        ranked = sorted(by_strike.items(), key=lambda item: abs(item[0] - spot_price))
        if not ranked:
            return spot_price
        weighted = []
        for strike, row in ranked[:5]:
            imbalance = row["ce_oi"] - row["pe_oi"]
            weight = abs(imbalance) + row["gamma"]
            weighted.append((strike, weight))
        total_weight = sum(weight for _, weight in weighted)
        if total_weight <= 0:
            return float(ranked[0][0])
        return sum(strike * weight for strike, weight in weighted) / total_weight

    def _gamma_density(self, by_strike: Dict[int, Dict[str, float]]) -> float:
        gamma_values = [row["gamma"] for row in by_strike.values() if row["gamma"] > 0]
        if not gamma_values:
            return 0.0
        avg_gamma = mean(gamma_values)
        peak_gamma = max(gamma_values)
        return max(0.0, min(1.0, peak_gamma / max(avg_gamma * 3, 1e-9)))
