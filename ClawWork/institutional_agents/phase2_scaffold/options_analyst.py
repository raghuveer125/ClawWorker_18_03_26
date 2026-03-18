from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from contracts import OptionChainInput, OptionRow, OptionsSignal


class OptionsAnalyst:
    def __init__(self, max_spread_bps: float = 50.0, min_oi: float = 1000.0, min_volume: float = 500.0):
        self.max_spread_bps = max_spread_bps
        self.min_oi = min_oi
        self.min_volume = min_volume

    @staticmethod
    def _avg(rows: List[OptionRow], field: str) -> float:
        vals = [getattr(row, field) for row in rows if getattr(row, field) is not None]
        return float(sum(vals) / len(vals)) if vals else 0.0

    def _liquidity_pass(self, rows: List[OptionRow]) -> bool:
        if not rows:
            return False
        return all(row.oi >= self.min_oi and row.volume >= self.min_volume for row in rows)

    def _spread_pass(self, rows: List[OptionRow]) -> bool:
        if not rows:
            return False
        return all(row.spread_bps <= self.max_spread_bps for row in rows)

    @staticmethod
    def _find_atm_straddle(rows: List[OptionRow]) -> Optional[Tuple[int, float]]:
        by_strike: Dict[int, Dict[str, OptionRow]] = {}
        for row in rows:
            strike_rows = by_strike.setdefault(row.strike, {})
            strike_rows[row.option_type] = row

        candidates: List[Tuple[int, float, float]] = []
        for strike, pair in by_strike.items():
            ce = pair.get("CE")
            pe = pair.get("PE")
            if ce and pe:
                straddle_price = ce.ltp + pe.ltp
                pair_volume = ce.volume + pe.volume
                candidates.append((strike, straddle_price, pair_volume))

        if not candidates:
            return None

        best_strike, best_straddle, _ = sorted(candidates, key=lambda item: item[2], reverse=True)[0]
        return best_strike, best_straddle

    def _greeks_score(self, rows: List[OptionRow], direction: str) -> float:
        avg_delta = self._avg(rows, "delta")
        avg_vega = self._avg(rows, "vega")

        if direction == "UP":
            score = (max(avg_delta, 0.0) * 100.0) + (avg_vega * 10.0)
        elif direction == "DOWN":
            score = (abs(min(avg_delta, 0.0)) * 100.0) + (avg_vega * 10.0)
        else:
            score = 25.0

        return float(max(0.0, min(score, 100.0)))

    @staticmethod
    def _vol_score(iv_percentile: float) -> float:
        if iv_percentile < 25:
            return 40.0
        if iv_percentile < 60:
            return 70.0
        if iv_percentile < 85:
            return 55.0
        return 35.0

    @staticmethod
    def _momentum_score(underlying_change_pct: float) -> float:
        abs_change = abs(underlying_change_pct)
        if abs_change >= 0.8:
            return 85.0
        if abs_change >= 0.4:
            return 65.0
        return 35.0

    @staticmethod
    def _straddle_score(direction: str, underlying_change_pct: float, has_straddle: bool) -> float:
        if not has_straddle:
            return 20.0

        abs_change = abs(underlying_change_pct)
        if direction in {"UP", "DOWN"} and abs_change >= 0.4:
            return 80.0
        if direction in {"UP", "DOWN"}:
            return 60.0
        return 35.0

    def analyze(self, chain_input: OptionChainInput) -> OptionsSignal:
        rows = chain_input.rows
        if not rows:
            return OptionsSignal(
                signal="NO_TRADE",
                confidence="LOW",
                preferred_strike_zone="NONE",
                options_score=0.0,
                momentum_score=0.0,
                greeks_score=0.0,
                volatility_score=0.0,
                liquidity_score=0.0,
                straddle_score=0.0,
                weighted_components={
                    "momentum": 0.0,
                    "greeks": 0.0,
                    "volatility": 0.0,
                    "liquidity": 0.0,
                    "straddle": 0.0,
                },
                atm_straddle_price=None,
                straddle_upper_band=None,
                straddle_lower_band=None,
                straddle_band_pct=chain_input.straddle_band_pct,
                rationale="No option-chain rows available.",
                liquidity_pass=False,
                spread_pass=False,
            )

        liquidity_pass = self._liquidity_pass(rows)
        spread_pass = self._spread_pass(rows)

        dir_hint = chain_input.straddle_breakout_direction
        momentum = self._momentum_score(chain_input.underlying_change_pct)
        greeks = self._greeks_score(rows, dir_hint)
        vol = self._vol_score(chain_input.iv_percentile)
        liq = 80.0 if liquidity_pass else 20.0

        atm_info = self._find_atm_straddle(rows)
        atm_straddle_price = atm_info[1] if atm_info else None
        straddle_upper_band = None
        straddle_lower_band = None
        if atm_straddle_price is not None:
            band_mult = chain_input.straddle_band_pct / 100.0
            straddle_upper_band = round(atm_straddle_price * (1.0 + band_mult), 2)
            straddle_lower_band = round(atm_straddle_price * (1.0 - band_mult), 2)

        straddle = self._straddle_score(
            dir_hint,
            chain_input.underlying_change_pct,
            has_straddle=atm_straddle_price is not None,
        )

        weighted_components = {
            "momentum": 0.20 * momentum,
            "greeks": 0.30 * greeks,
            "volatility": 0.20 * vol,
            "liquidity": 0.15 * liq,
            "straddle": 0.15 * straddle,
        }
        options_score = sum(weighted_components.values())

        if not (liquidity_pass and spread_pass):
            signal = "NO_TRADE"
            confidence = "LOW"
            zone = "NONE"
            rationale = "Liquidity or spread guard failed."
        elif dir_hint == "UP" and options_score >= 55:
            signal = "BULLISH"
            confidence = "HIGH" if options_score >= 70 else "MEDIUM"
            zone = "OTM_1" if confidence == "HIGH" else "ATM"
            rationale = f"Bullish options profile (score={options_score:.2f})."
        elif dir_hint == "DOWN" and options_score >= 55:
            signal = "BEARISH"
            confidence = "HIGH" if options_score >= 70 else "MEDIUM"
            zone = "OTM_1" if confidence == "HIGH" else "ATM"
            rationale = f"Bearish options profile (score={options_score:.2f})."
        else:
            signal = "NEUTRAL"
            confidence = "LOW"
            zone = "NONE"
            rationale = f"No strong options edge (score={options_score:.2f})."

        return OptionsSignal(
            signal=signal,
            confidence=confidence,
            preferred_strike_zone=zone,
            options_score=round(options_score, 2),
            momentum_score=round(momentum, 2),
            greeks_score=round(greeks, 2),
            volatility_score=round(vol, 2),
            liquidity_score=round(liq, 2),
            straddle_score=round(straddle, 2),
            weighted_components={key: round(value, 2) for key, value in weighted_components.items()},
            atm_straddle_price=round(atm_straddle_price, 2) if atm_straddle_price is not None else None,
            straddle_upper_band=straddle_upper_band,
            straddle_lower_band=straddle_lower_band,
            straddle_band_pct=chain_input.straddle_band_pct,
            rationale=rationale,
            liquidity_pass=liquidity_pass,
            spread_pass=spread_pass,
        )
