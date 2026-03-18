"""
Volatility surface analysis for strike placement, sizing, and targets.
"""

from collections import defaultdict
from math import sqrt
from statistics import mean
from typing import Any, DefaultDict, Dict, List, Optional

from ..base import BaseBot, BotContext, BotResult, BotStatus
from ..config import ScalpingConfig


class VolatilitySurfaceAgent(BaseBot):
    BOT_TYPE = "volatility_surface"
    REQUIRES_LLM = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._iv_history: DefaultDict[str, List[float]] = defaultdict(list)

    def get_description(self) -> str:
        return "Builds volatility surface controls for OTM distance, size, and target spacing"

    async def execute(self, context: BotContext) -> BotResult:
        config = context.data.get("config", ScalpingConfig())
        option_chains = context.data.get("option_chains", {})
        candles_1m = context.data.get("candles_1m", {})
        vix = float(context.data.get("vix", 15.0) or 15.0)

        surface: Dict[str, Dict[str, float]] = {}
        for symbol, chain in option_chains.items():
            avg_iv = self._average_iv(chain)
            if avg_iv is not None:
                history = self._iv_history[symbol]
                history.append(avg_iv)
                if len(history) > config.vol_surface_iv_lookback:
                    history[:] = history[-config.vol_surface_iv_lookback :]

            iv_percentile = self._iv_percentile(symbol, avg_iv)
            realized_vol = self._realized_vol(candles_1m.get(symbol, []), config)
            term_slope = self._term_structure_slope(chain)
            surface_score = self._surface_score(vix, iv_percentile, realized_vol, term_slope)

            otm_scale = 1.0
            if iv_percentile >= 0.75:
                otm_scale *= 0.85
            elif iv_percentile <= 0.25:
                otm_scale *= 1.10

            size_scale = 1.0
            if realized_vol >= config.high_realized_vol_level:
                size_scale *= 0.8
            elif realized_vol <= (config.high_realized_vol_level * 0.5):
                size_scale *= 1.05

            target_scale = 1.0
            if surface_score >= 0.70:
                target_scale *= 1.15
            elif surface_score <= 0.35:
                target_scale *= 0.90

            surface[symbol] = {
                "surface_score": round(surface_score, 4),
                "vix": round(vix, 4),
                "iv_percentile": round(iv_percentile, 4),
                "realized_vol": round(realized_vol, 6),
                "term_structure_slope": round(term_slope, 6),
                "otm_scale": round(otm_scale, 4),
                "size_scale": round(size_scale, 4),
                "target_scale": round(target_scale, 4),
            }

        context.data["volatility_surface"] = surface

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS,
            output={"symbols": surface},
            metrics={"symbols_analyzed": len(surface)},
        )

    def _average_iv(self, chain: Any) -> Optional[float]:
        values = [float(getattr(opt, "iv", 0) or 0) for opt in getattr(chain, "options", []) if float(getattr(opt, "iv", 0) or 0) > 0]
        return mean(values) if values else None

    def _iv_percentile(self, symbol: str, current_iv: Optional[float]) -> float:
        if current_iv is None:
            return 0.5
        history = self._iv_history.get(symbol, [])
        if len(history) < 5:
            return 0.5
        below = sum(1 for value in history if value <= current_iv)
        return below / max(len(history), 1)

    def _realized_vol(self, candles: List[Dict[str, Any]], config: ScalpingConfig) -> float:
        closes = [float(candle.get("close", 0) or 0) for candle in candles[-config.vol_surface_realized_window :]]
        closes = [value for value in closes if value > 0]
        if len(closes) < 3:
            return 0.0
        returns = []
        for previous, current in zip(closes, closes[1:]):
            if previous > 0:
                returns.append((current - previous) / previous)
        if len(returns) < 2:
            return 0.0
        avg = sum(returns) / len(returns)
        variance = sum((ret - avg) ** 2 for ret in returns) / len(returns)
        return sqrt(max(variance, 0.0))

    def _term_structure_slope(self, chain: Any) -> float:
        expiries: DefaultDict[str, List[float]] = defaultdict(list)
        for opt in getattr(chain, "options", []):
            expiry = str(getattr(opt, "expiry", "") or "")
            iv = float(getattr(opt, "iv", 0) or 0)
            if expiry and iv > 0:
                expiries[expiry].append(iv)
        if len(expiries) < 2:
            return 0.0
        ordered = sorted(expiries.keys())
        near = mean(expiries[ordered[0]])
        nxt = mean(expiries[ordered[1]])
        return nxt - near

    def _surface_score(self, vix: float, iv_percentile: float, realized_vol: float, term_slope: float) -> float:
        normalized_vix = max(0.0, min(1.0, (vix - 10.0) / 20.0))
        normalized_rv = max(0.0, min(1.0, realized_vol / 0.03))
        normalized_slope = max(0.0, min(1.0, (term_slope + 0.10) / 0.20))
        score = (normalized_vix * 0.30) + (iv_percentile * 0.35) + (normalized_rv * 0.25) + (normalized_slope * 0.10)
        return max(0.0, min(1.0, score))
