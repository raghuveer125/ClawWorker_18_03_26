import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import unittest


SCALPING_ROOT = Path(__file__).resolve().parent
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping.agents.analysis_agents import MomentumSignal, StrikeSelectorAgent, StructureAgent
from scalping.agents.analysis_agents import StrikeSelection
from scalping.agents.data_agents import DataFeedAgent, OptionChainAgent, SpotData
from scalping.agents.infrastructure_agents import LiquidityMonitorAgent
from scalping.agents.signal_quality_agent import SignalQualityAgent
from scalping.base import BotContext
from scalping.config import ScalpingConfig


@dataclass
class _Option:
    symbol: str
    strike: int
    option_type: str
    ltp: float
    bid: float
    ask: float
    volume: int
    oi: int
    oi_change: int = 0
    delta: float = 0.0
    gamma: float = 0.0
    spread: float = 0.0
    spread_pct: float = 0.0


@dataclass
class _Chain:
    underlying: str
    spot_price: float
    atm_strike: int
    options: list


class LivePipelineQualityTests(unittest.TestCase):
    def test_live_candles_use_ltp_not_session_high_low(self):
        agent = DataFeedAgent(symbols=["NSE:NIFTY50-INDEX"])
        context = BotContext(data={})
        symbol = "NSE:NIFTY50-INDEX"
        minute = datetime(2026, 3, 18, 13, 40, 10)

        first = SpotData(
            symbol=symbol,
            ltp=23830.0,
            open=23750.0,
            high=24050.0,
            low=23510.0,
            prev_close=23790.0,
            volume=100,
            vwap=23810.0,
            change_pct=0.2,
            timestamp=minute,
        )
        second = SpotData(
            symbol=symbol,
            ltp=23836.0,
            open=23750.0,
            high=24050.0,
            low=23510.0,
            prev_close=23790.0,
            volume=120,
            vwap=23812.0,
            change_pct=0.25,
            timestamp=minute.replace(second=35),
        )

        agent._update_candle_context(context, {symbol: first})
        agent._update_candle_context(context, {symbol: second})

        candle = context.data["candles_1m"][symbol][-1]
        self.assertEqual(candle["open"], 23830.0)
        self.assertEqual(candle["high"], 23836.0)
        self.assertEqual(candle["low"], 23830.0)
        self.assertEqual(candle["close"], 23836.0)

    def test_structure_warms_up_instead_of_generating_random_live_breaks(self):
        agent = StructureAgent()
        symbol = "NSE:NIFTY50-INDEX"
        spot = type("Spot", (), {"ltp": 23830.0, "vwap": 23810.0})()
        context = BotContext(data={f"candles_{symbol}": []})

        structure = asyncio.run(agent._analyze_structure(symbol, spot, context))

        self.assertIsNone(structure["break"])
        self.assertIn("structure warming up", structure["summary"])
        self.assertEqual(structure["swing_highs"], [])
        self.assertEqual(structure["swing_lows"], [])

    def test_structure_derives_1m_3m_5m_alignment_state(self):
        agent = StructureAgent()
        symbol = "NSE:NIFTY50-INDEX"
        candles = [
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "timestamp": "2026-03-18T09:15:00"},
            {"open": 100.5, "high": 101.2, "low": 100.2, "close": 101.0, "timestamp": "2026-03-18T09:16:00"},
            {"open": 101.0, "high": 101.6, "low": 100.8, "close": 101.3, "timestamp": "2026-03-18T09:17:00"},
            {"open": 101.3, "high": 101.7, "low": 101.0, "close": 101.4, "timestamp": "2026-03-18T09:18:00"},
            {"open": 101.4, "high": 101.9, "low": 101.1, "close": 101.6, "timestamp": "2026-03-18T09:19:00"},
            {"open": 101.6, "high": 103.5, "low": 101.5, "close": 103.2, "timestamp": "2026-03-18T09:20:00"},
        ]
        spot = type("Spot", (), {"ltp": 103.2, "vwap": 101.4})()
        context = BotContext(data={f"candles_{symbol}": candles})

        structure = asyncio.run(agent._analyze_structure(symbol, spot, context))

        self.assertEqual(structure["timeframes"]["1m"]["trend"], "bullish")
        self.assertEqual(structure["timeframes"]["3m"]["breakout"], "bullish")
        self.assertEqual(structure["timeframes"]["5m"]["trend"], "bullish")
        self.assertTrue(structure["timeframe_alignment"]["three_tf_aligned"])

    def test_live_strike_selector_falls_back_when_greeks_missing_and_otm_has_shifted(self):
        selector = StrikeSelectorAgent()
        symbol = "NSE:NIFTY50-INDEX"
        chain = _Chain(
            underlying=symbol,
            spot_price=23830.8,
            atm_strike=23850,
            options=[
                _Option(
                    symbol="NSE:NIFTY26MAR24600CE",
                    strike=24600,
                    option_type="CE",
                    ltp=18.5,
                    bid=18.5,
                    ask=18.6,
                    volume=22018295,
                    oi=2766205,
                    spread=0.1,
                    spread_pct=0.54,
                ),
                _Option(
                    symbol="NSE:NIFTY26MAR24550CE",
                    strike=24550,
                    option_type="CE",
                    ltp=21.8,
                    bid=21.9,
                    ask=21.95,
                    volume=14760460,
                    oi=1115790,
                    spread=0.05,
                    spread_pct=0.23,
                ),
            ],
        )

        context = BotContext(
            data={
                "option_chains": {symbol: chain},
                "spot_data": {symbol: type("Spot", (), {"ltp": chain.spot_price})()},
                "market_structure": {symbol: {"trend": "bullish"}},
                "momentum_signals": [],
                "config": ScalpingConfig(),
                "vix": 18.85,
                "volatility_surface": {},
                "dealer_pressure": {},
            }
        )

        result = asyncio.run(selector.execute(context))

        self.assertGreater(result.output["total_selections"], 0)
        first = context.data["strike_selections"][symbol][0]
        self.assertIn("Adaptive premium-band fallback", first.reasons)
        self.assertGreater(first.confidence, 0.0)

    def test_quality_uses_directional_momentum_and_regime_volume_fallback(self):
        agent = SignalQualityAgent()
        quality = agent._calculate_quality(
            signal={"symbol": "NSE:NIFTY50-INDEX", "option_type": "CE", "confidence": 0.8, "premium": 18.5},
            signal_confidence=0.8,
            option_type="CE",
            regime="TRENDING_BULLISH",
            volume_data={},
            liquidity_data={"liquidity_score": 0.7, "spread_pct": 0.4},
            momentum_signals=[
                MomentumSignal(
                    symbol="NSE:NIFTY50-INDEX",
                    signal_type="futures_surge",
                    strength=0.9,
                    price_move=32.0,
                    volume_multiple=0.0,
                    option_expansion_pct=0.0,
                    timestamp=datetime.now(),
                    direction="bullish",
                )
            ],
            config=ScalpingConfig(),
            weights={
                "confidence": 0.25,
                "regime": 0.20,
                "volume": 0.15,
                "liquidity": 0.15,
                "momentum": 0.15,
                "risk": 0.10,
            },
            volatility_surface={},
            dealer_pressure={},
        )

        self.assertGreaterEqual(quality.momentum_score, 0.7)
        self.assertTrue(quality.pass_filter)

    def test_quality_preserves_underlying_symbol_for_liquidity_matching(self):
        symbol = "NSE:NIFTY50-INDEX"
        option_symbol = "NSE:NIFTY26MAR24600CE"
        parser = OptionChainAgent(symbols=[symbol])
        parsed = parser._parse_option_chain(
            {
                "optionsChain": [
                    {"symbol": symbol, "ltp": 23830.8, "option_type": "", "strike_price": -1},
                    {
                        "symbol": option_symbol,
                        "strike_price": 24600,
                        "option_type": "CE",
                        "ltp": 18.5,
                        "bid": 18.5,
                        "ask": 18.6,
                        "oi": 2766205,
                        "volume": 22018295,
                    },
                ]
            },
            symbol,
            23830.8,
        )
        context = BotContext(
            data={
                "strike_selections": {
                    symbol: [
                        StrikeSelection(
                            symbol=option_symbol,
                            strike=24600,
                            option_type="CE",
                            premium=18.5,
                            delta=0.18,
                            spread=0.1,
                            spread_pct=0.54,
                            volume=22018295,
                            oi=2766205,
                            score=0.9,
                            reasons=["test"],
                            confidence=0.9,
                            entry=18.6,
                        )
                    ]
                },
                "option_chains": {symbol: parsed},
                "market_regimes": {symbol: {"regime": "TRENDING_BULLISH", "factors": {"volume_acceleration": 1.4, "volume_trend": "rising"}}},
                "momentum_signals": [
                    MomentumSignal(
                        symbol=symbol,
                        signal_type="futures_surge",
                        strength=0.9,
                        price_move=32.0,
                        volume_multiple=1.8,
                        option_expansion_pct=0.0,
                        timestamp=datetime.now(),
                        direction="bullish",
                    )
                ],
                "config": ScalpingConfig(),
            }
        )

        quality_result = asyncio.run(SignalQualityAgent().execute(context))

        self.assertEqual(quality_result.output["signals_passed"], 1)
        passed = context.data["quality_filtered_signals"][0]
        self.assertEqual(passed["symbol"], symbol)
        self.assertEqual(passed["underlying_symbol"], symbol)
        self.assertEqual(passed["option_symbol"], option_symbol)

        liquidity_result = asyncio.run(LiquidityMonitorAgent().execute(context))

        self.assertEqual(liquidity_result.output["illiquid_selected_count"], 0)
        self.assertEqual(len(context.data["liquidity_filtered_selections"]), 1)

    def test_option_chain_parser_backfills_missing_greeks_and_depth(self):
        agent = OptionChainAgent(symbols=["NSE:NIFTY50-INDEX"])
        parsed = agent._parse_option_chain(
            {
                "optionsChain": [
                    {"symbol": "NSE:NIFTY50-INDEX", "ltp": 23830.8, "option_type": "", "strike_price": -1},
                    {
                        "symbol": "NSE:NIFTY26MAR24600CE",
                        "strike_price": 24600,
                        "option_type": "CE",
                        "ltp": 18.5,
                        "bid": 18.5,
                        "ask": 18.6,
                        "oi": 2766205,
                        "volume": 22018295,
                    },
                ]
            },
            "NSE:NIFTY50-INDEX",
            23830.8,
        )

        option = parsed.options[0]
        self.assertGreater(abs(option.delta), 0.01)
        self.assertGreater(option.gamma, 0.0)
        self.assertGreaterEqual(option.bid_qty, 100)
        self.assertGreaterEqual(option.ask_qty, 100)

    def test_liquidity_monitor_accepts_tight_live_quotes_without_explicit_depth(self):
        parser = OptionChainAgent(symbols=["NSE:NIFTY50-INDEX"])
        parsed = parser._parse_option_chain(
            {
                "optionsChain": [
                    {"symbol": "NSE:NIFTY50-INDEX", "ltp": 23830.8, "option_type": "", "strike_price": -1},
                    {
                        "symbol": "NSE:NIFTY26MAR24600CE",
                        "strike_price": 24600,
                        "option_type": "CE",
                        "ltp": 18.5,
                        "bid": 18.5,
                        "ask": 18.6,
                        "oi": 2766205,
                        "volume": 22018295,
                    },
                ]
            },
            "NSE:NIFTY50-INDEX",
            23830.8,
        )
        signal = {
            "symbol": "NSE:NIFTY50-INDEX",
            "strike": 24600,
            "option_type": "CE",
            "confidence": 0.9,
            "quality_score": 0.6,
        }
        context = BotContext(
            data={
                "option_chains": {"NSE:NIFTY50-INDEX": parsed},
                "quality_filtered_signals": [signal],
                "rejected_signals": [],
                "config": ScalpingConfig(),
            }
        )

        result = asyncio.run(LiquidityMonitorAgent().execute(context))

        self.assertEqual(result.output["illiquid_selected_count"], 0)
        self.assertEqual(len(context.data["liquidity_filtered_selections"]), 1)


if __name__ == "__main__":
    unittest.main()
