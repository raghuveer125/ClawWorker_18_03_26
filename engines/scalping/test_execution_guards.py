import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import unittest


SCALPING_ROOT = Path(__file__).resolve().parent
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping.agents.analysis_agents import MomentumSignal, StructureBreak
from scalping.agents.execution_agents import EntryAgent, ExitAgent, Order, Position, PositionManagerAgent
from scalping.agents.meta_agents import CorrelationGuardAgent
from scalping.api import _normalize_trade
from scalping.base import BotContext, BotStatus
from scalping.config import ScalpingConfig


@dataclass
class _OptionQuote:
    strike: int
    option_type: str
    ltp: float
    bid: float = 0.0
    spread_pct: float = 0.0


@dataclass
class _OptionChain:
    options: list


class ExecutionGuardTests(unittest.TestCase):
    def test_correlation_guard_scales_clustered_same_batch_signals(self):
        symbol = "BSE:SENSEX-INDEX"
        context = BotContext(
            data={
                "positions": [],
                "pending_orders": [],
                "liquidity_filtered_selections": [
                    {"symbol": symbol, "strike": 74200, "option_type": "PE", "confidence": 0.92},
                    {"symbol": symbol, "strike": 74300, "option_type": "PE", "confidence": 0.90},
                    {"symbol": "NSE:NIFTY50-INDEX", "strike": 23800, "option_type": "CE", "confidence": 0.88},
                ],
                "config": ScalpingConfig(),
            }
        )

        result = asyncio.run(CorrelationGuardAgent().execute(context))

        blocked = set(context.data["correlation_blocked_signal_keys"])
        penalties = context.data["correlation_signal_penalties"]
        self.assertEqual(result.metrics["orders_blocked"], 0)
        self.assertEqual(result.metrics["signals_adjusted"], 1)
        self.assertEqual(blocked, set())
        self.assertEqual(penalties[f"{symbol}|74300|PE"]["risk"], "high")
        self.assertAlmostEqual(penalties[f"{symbol}|74300|PE"]["size_scale"], 0.5, places=2)
        self.assertNotIn("NSE:NIFTY50-INDEX|23800|CE", penalties)

    def test_entry_agent_caps_new_orders_within_same_batch(self):
        symbol = "NSE:NIFTY50-INDEX"
        config = ScalpingConfig(
            max_positions=3,
            entry_lots=1,
            require_structure_break=False,
            require_futures_confirm=False,
            require_volume_burst=False,
        )
        positions = [
            Position(
                position_id="POS_1",
                symbol=symbol,
                strike=23700,
                option_type="CE",
                entry_price=18.0,
                entry_time=datetime.now(),
                quantity=75,
                lots=1,
                lot_size=75,
                direction="long",
                status="open",
            ),
            Position(
                position_id="POS_2",
                symbol=symbol,
                strike=23800,
                option_type="CE",
                entry_price=19.0,
                entry_time=datetime.now(),
                quantity=75,
                lots=1,
                lot_size=75,
                direction="long",
                status="open",
            ),
        ]
        context = BotContext(
            data={
                "config": config,
                "positions": positions,
                "replay_mode": True,
                "liquidity_filtered_selections": [
                    {
                        "symbol": symbol,
                        "strike": 23900,
                        "option_type": "CE",
                        "premium": 20.0,
                        "entry": 20.0,
                        "sl": 17.0,
                        "t1": 24.5,
                        "confidence": 0.90,
                        "status": "APPROVED",
                        "action": "Take",
                    },
                    {
                        "symbol": symbol,
                        "strike": 24000,
                        "option_type": "CE",
                        "premium": 18.5,
                        "entry": 18.5,
                        "sl": 15.5,
                        "t1": 23.0,
                        "confidence": 0.86,
                        "status": "APPROVED",
                        "action": "Take",
                    },
                ],
                "structure_breaks": [
                    StructureBreak(
                        symbol=symbol,
                        break_type="bos_bullish",
                        break_price=23900,
                        previous_level=23800,
                        strength=0.8,
                        timestamp=datetime.now(),
                    )
                ],
                "momentum_signals": [
                    MomentumSignal(
                        symbol=symbol,
                        signal_type="futures_surge",
                        strength=0.9,
                        price_move=45.0,
                        volume_multiple=0.0,
                        option_expansion_pct=0.0,
                        timestamp=datetime.now(),
                        direction="bullish",
                    )
                ],
                "trap_signals": [],
                "market_structure": {
                    symbol: {
                        "trend": "bullish",
                        "timeframes": {
                            "1m": {"available": True, "trend": "bullish", "momentum_points": 5.0},
                            "3m": {"available": True, "trend": "bullish", "breakout": "bullish"},
                            "5m": {"available": True, "trend": "bullish"},
                        },
                    }
                },
                "volatility_burst": {
                    f"{symbol}|23900|CE": {"active": True},
                    f"{symbol}|24000|CE": {"active": True},
                },
                "liquidity_vacuum": {
                    f"{symbol}|23900|CE": {"active": False},
                    f"{symbol}|24000|CE": {"active": False},
                },
                "momentum_strength": {
                    f"{symbol}|23900|CE": {"score": 0.7, "timing": "immediate"},
                    f"{symbol}|24000|CE": {"score": 0.7, "timing": "immediate"},
                },
                "queue_risk": {
                    f"{symbol}|23900|CE": {"size_scale": 1.0, "risk": "low"},
                    f"{symbol}|24000|CE": {"size_scale": 1.0, "risk": "low"},
                },
            }
        )

        result = asyncio.run(EntryAgent(dry_run=True).execute(context))

        self.assertEqual(result.output["orders_created"], 1)
        self.assertEqual(len(context.data["pending_orders"]), 1)
        self.assertTrue(
            any(
                "Position cap reached during batch" in reason
                for rejected in context.data["rejected_signals"]
                for reason in rejected.get("rejection_reasons", [])
            )
        )

    def test_entry_agent_rejects_new_live_entries_after_cutoff(self):
        symbol = "BSE:SENSEX-INDEX"
        context = BotContext(
            data={
                "config": ScalpingConfig(late_entry_cutoff_time="14:50"),
                "cycle_timestamp": "2026-03-18T14:55:03",
                "liquidity_filtered_selections": [
                    {"symbol": symbol, "strike": 74200, "option_type": "PE", "premium": 15.05}
                ],
                "positions": [],
            }
        )

        result = asyncio.run(EntryAgent(dry_run=True).execute(context))

        self.assertEqual(result.status, BotStatus.SKIPPED)
        self.assertEqual(context.data["pending_orders"], [])
        self.assertTrue(
            any(
                "Past late-entry cutoff: 14:50" in reason
                for rejected in context.data["rejected_signals"]
                for reason in rejected.get("rejection_reasons", [])
            )
        )

    def test_entry_agent_rejects_replay_signal_without_live_market_conditions(self):
        symbol = "NSE:NIFTY50-INDEX"
        context = BotContext(
            data={
                "config": ScalpingConfig(
                    require_structure_break=True,
                    require_futures_confirm=True,
                    require_volume_burst=False,
                    replay_require_market_conditions=True,
                ),
                "replay_mode": True,
                "positions": [],
                "liquidity_filtered_selections": [
                    {
                        "symbol": symbol,
                        "strike": 24600,
                        "option_type": "PE",
                        "premium": 198.8,
                        "confidence": 0.95,
                        "source": "replay_journal",
                        "status": "APPROVED",
                        "action": "Take",
                        "entry_ready": "Y",
                    }
                ],
                "structure_breaks": [],
                "momentum_signals": [],
                "trap_signals": [],
            }
        )

        result = asyncio.run(EntryAgent(dry_run=True).execute(context))

        self.assertEqual(result.output["orders_created"], 0)
        self.assertEqual(context.data["pending_orders"], [])
        self.assertTrue(
            any(
                "Replay journal approval requires at least 1 live market condition" in reason
                for rejected in context.data["rejected_signals"]
                for reason in rejected.get("rejection_reasons", [])
            )
        )

    def test_entry_agent_accepts_strict_a_plus_setup_and_scales_size_from_setup(self):
        symbol = "BSE:SENSEX-INDEX"
        signal_key = f"{symbol}|76500|CE"
        context = BotContext(
            data={
                "config": ScalpingConfig(
                    entry_lots=4,
                    strict_a_plus_only=True,
                    strict_a_plus_size_fraction=0.65,
                    require_structure_break=True,
                    require_futures_confirm=True,
                    require_volume_burst=False,
                ),
                "positions": [],
                "cycle_timestamp": "2026-03-18T09:37:14",
                "liquidity_filtered_selections": [
                    {
                        "symbol": symbol,
                        "strike": 76500,
                        "option_type": "CE",
                        "premium": 368.91,
                        "entry": 368.91,
                        "sl": 340.0,
                        "t1": 408.0,
                        "confidence": 0.95,
                        "quality_score": 0.7,
                    }
                ],
                "structure_breaks": [
                    StructureBreak(
                        symbol=symbol,
                        break_type="bos_bullish",
                        break_price=76480,
                        previous_level=76350,
                        strength=0.9,
                        timestamp=datetime.now(),
                    )
                ],
                "momentum_signals": [
                    MomentumSignal(
                        symbol=symbol,
                        signal_type="futures_surge",
                        strength=0.95,
                        price_move=120.0,
                        volume_multiple=0.0,
                        option_expansion_pct=0.0,
                        timestamp=datetime.now(),
                        direction="bullish",
                    )
                ],
                "trap_signals": [],
                "market_structure": {
                    symbol: {
                        "trend": "bullish",
                        "timeframes": {
                            "1m": {"available": True, "trend": "bullish", "momentum_points": 14.0},
                            "3m": {"available": True, "trend": "bullish", "breakout": "bullish"},
                            "5m": {"available": True, "trend": "bullish"},
                        },
                    }
                },
                "volatility_burst": {signal_key: {"active": True}},
                "liquidity_vacuum": {signal_key: {"active": False}},
                "momentum_strength": {signal_key: {"score": 0.8, "timing": "immediate"}},
                "entry_confirmation_state": {signal_key: {"status": "confirmed"}},
                "queue_risk": {signal_key: {"size_scale": 1.0, "risk": "low"}},
            }
        )

        result = asyncio.run(EntryAgent(dry_run=True).execute(context))

        self.assertEqual(result.output["orders_created"], 1)
        order = context.data["pending_orders"][0]
        self.assertEqual(order.metadata["setup_tag"], "A+")
        self.assertTrue(order.metadata["strict_filter_pass"])
        self.assertEqual(order.metadata["lots"], 3)
        self.assertAlmostEqual(order.metadata["rr_ratio"], (408.0 - 368.91) / (368.91 - 340.0), places=3)

    def test_entry_agent_accepts_b_setup_and_reduces_size_for_correlation(self):
        symbol = "BSE:SENSEX-INDEX"
        signal_key = f"{symbol}|76500|CE"
        context = BotContext(
            data={
                "config": ScalpingConfig(
                    entry_lots=4,
                    strict_a_plus_only=False,
                    strict_b_rr_ratio=1.1,
                    strict_b_size_fraction=0.35,
                    require_structure_break=True,
                    require_futures_confirm=True,
                    require_volume_burst=False,
                ),
                "positions": [],
                "cycle_timestamp": "2026-03-18T10:02:15",
                "liquidity_filtered_selections": [
                    {
                        "symbol": symbol,
                        "strike": 76500,
                        "option_type": "CE",
                        "premium": 100.0,
                        "entry": 100.0,
                        "sl": 90.0,
                        "t1": 112.0,
                        "confidence": 0.88,
                        "quality_score": 0.7,
                    }
                ],
                "structure_breaks": [
                    StructureBreak(
                        symbol=symbol,
                        break_type="bos_bullish",
                        break_price=76480,
                        previous_level=76350,
                        strength=0.9,
                        timestamp=datetime.now(),
                    )
                ],
                "momentum_signals": [
                    MomentumSignal(
                        symbol=symbol,
                        signal_type="futures_surge",
                        strength=0.82,
                        price_move=120.0,
                        volume_multiple=0.0,
                        option_expansion_pct=0.0,
                        timestamp=datetime.now(),
                        direction="bullish",
                    )
                ],
                "trap_signals": [],
                "market_structure": {
                    symbol: {
                        "trend": "bullish",
                        "timeframes": {
                            "1m": {"available": True, "trend": "bullish", "momentum_points": 6.0},
                            "3m": {"available": True, "trend": "bullish", "breakout": "bullish"},
                            "5m": {"available": True, "trend": "bearish"},
                        },
                    }
                },
                "volatility_burst": {signal_key: {"active": False}},
                "liquidity_vacuum": {signal_key: {"active": False}},
                "momentum_strength": {signal_key: {"score": 0.45, "timing": "watch"}},
                "entry_confirmation_state": {signal_key: {"status": "pending"}},
                "queue_risk": {signal_key: {"size_scale": 1.0, "risk": "low"}},
                "correlation_signal_penalties": {signal_key: {"size_scale": 0.5, "risk": "high"}},
            }
        )

        result = asyncio.run(EntryAgent(dry_run=True).execute(context))

        self.assertEqual(result.output["orders_created"], 1)
        order = context.data["pending_orders"][0]
        self.assertEqual(order.metadata["setup_tag"], "B")
        self.assertTrue(order.metadata["strict_filter_pass"])
        self.assertEqual(order.metadata["lots"], 1)
        self.assertEqual(order.metadata["correlation_penalty"]["risk"], "high")
        self.assertAlmostEqual(order.metadata["rr_ratio"], 1.2, places=2)

    def test_exit_agent_flattens_when_thesis_support_is_missing_for_three_cycles(self):
        symbol = "BSE:SENSEX-INDEX"
        position = Position(
            position_id="POS_sensex_pe",
            symbol=symbol,
            strike=74200,
            option_type="PE",
            entry_price=15.05,
            entry_time=datetime.now(),
            quantity=20,
            lots=1,
            lot_size=20,
            direction="long",
            status="open",
            sl_price=10.55,
            target_price=20.05,
        )
        option_chains = {
            symbol: _OptionChain(
                options=[_OptionQuote(strike=74200, option_type="PE", ltp=15.50, bid=15.45, spread_pct=0.2)]
            )
        }
        agent = ExitAgent(dry_run=True)
        config = ScalpingConfig(thesis_invalidation_cycles=3)

        for expected_exit_orders in (0, 0, 1):
            context = BotContext(
                data={
                    "config": config,
                    "positions": [position],
                    "option_chains": option_chains,
                    "strike_selections": {},
                    "quality_filtered_signals": [],
                    "liquidity_filtered_selections": [],
                }
            )
            asyncio.run(agent.execute(context))
            self.assertEqual(len(context.data["exit_orders"]), expected_exit_orders)

        thesis_order = context.data["exit_orders"][0]
        self.assertTrue(thesis_order.order_id.startswith("THESIS_"))
        self.assertEqual(context.data["position_updates"][0]["action"], "thesis_invalidated")
        self.assertIn("strike", thesis_order.reason)
        self.assertIn("quality", thesis_order.reason)
        self.assertIn("liquidity", thesis_order.reason)

    def test_replay_fill_and_time_stop_use_cycle_timestamp(self):
        symbol = "BSE:SENSEX-INDEX"
        cycle_time = datetime(2026, 3, 6, 11, 44, 35)
        config = ScalpingConfig(
            entry_lots=1,
            require_structure_break=False,
            require_futures_confirm=False,
            require_volume_burst=False,
            exit_time_stop_minutes=10,
        )
        context = BotContext(
            data={
                "config": config,
                "replay_mode": True,
                "cycle_timestamp": cycle_time.isoformat(),
            }
        )

        order = asyncio.run(
            EntryAgent(dry_run=True)._create_entry_order(
                signal=type(
                    "ReplaySignal",
                    (),
                    {
                        "symbol": symbol,
                        "direction": "PE",
                        "strike": 74200,
                        "premium": 15.05,
                        "lots": 1,
                        "confidence": 0.9,
                        "conditions_met": ["historical_signal_approved", "historical_entry_ready"],
                        "timestamp": cycle_time,
                    },
                )(),
                symbol=symbol,
                config=config,
                multiplier=1.0,
                replay_mode=True,
                current_time=cycle_time,
                metadata={"ask": 15.05},
            )
        )

        self.assertEqual(order.fill_time, cycle_time)

        position = Position(
            position_id="POS_time_stop",
            symbol=symbol,
            strike=74200,
            option_type="PE",
            entry_price=15.05,
            entry_time=cycle_time,
            quantity=20,
            lots=1,
            lot_size=20,
            direction="long",
            status="open",
        )
        time_stop = ExitAgent(dry_run=True)._check_time_stop(
            position,
            current_price=14.50,
            config=config,
            current_time=cycle_time.replace(minute=55),
        )

        self.assertIsNotNone(time_stop)
        self.assertIn("Time stop", time_stop.reason)

    def test_position_manager_persists_decision_packet_for_postmortem(self):
        symbol = "BSE:SENSEX-INDEX"
        timestamp = datetime(2026, 3, 18, 14, 55, 3)
        order = Order(
            order_id="ENT_packet",
            symbol=symbol,
            strike=74200,
            option_type="PE",
            order_type="market",
            side="buy",
            quantity=20,
            price=15.05,
            fill_price=15.05,
            fill_time=timestamp,
            status="simulated",
            reason="Entry: structure_break, futures_momentum",
            metadata={
                "conditions_met": ["structure_break", "futures_momentum"],
                "condition_count": 2,
                "confidence": 0.91,
                "quality_grade": "A",
                "quality_score": 0.88,
                "spread_pct": 0.25,
                "momentum_strength": 0.84,
                "entry_confirmation": {"status": "confirmed"},
                "queue_risk": {"risk": "low"},
                "fill_quote": {"bid": 15.0, "ask": 15.05},
            },
        )
        context = BotContext(
            data={
                "config": ScalpingConfig(),
                "cycle_timestamp": "2026-03-18T14:55:03",
                "engine_mode": "LIVE PAPER",
                "market_regimes": {symbol: {"regime": "TRENDING_BEARISH"}},
                "market_structure": {symbol: {"trend": "bearish", "break": "mss_down"}},
                "strike_selections": {
                    symbol: [
                        {"underlying_symbol": symbol, "strike": 74200, "option_type": "PE"}
                    ]
                },
                "quality_filtered_signals": [
                    {"underlying_symbol": symbol, "strike": 74200, "option_type": "PE"}
                ],
                "liquidity_filtered_selections": [
                    {"underlying_symbol": symbol, "strike": 74200, "option_type": "PE"}
                ],
            }
        )

        manager = PositionManagerAgent()
        manager._create_position(order, context)
        trade = next(iter(manager._trade_records.values()))
        packet = trade["decision_packet"]

        self.assertEqual(packet["signal_key"], f"{symbol}|74200|PE")
        self.assertEqual(packet["engine_mode"], "LIVE PAPER")
        self.assertEqual(packet["conditions_met"], ["structure_break", "futures_momentum"])
        self.assertTrue(all(packet["stage_support"].values()))
        self.assertEqual(trade["agent_decisions"][0]["phase"], "entry")
        self.assertEqual(trade["agent_decisions"][0]["decision_packet"]["signal_key"], packet["signal_key"])

    def test_position_manager_flattens_open_positions_at_replay_end(self):
        symbol = "BSE:SENSEX-INDEX"
        cycle_time = datetime(2026, 3, 6, 15, 25, 0)
        order = Order(
            order_id="ENT_flatten",
            symbol=symbol,
            strike=74200,
            option_type="PE",
            order_type="market",
            side="buy",
            quantity=20,
            price=15.05,
            fill_price=15.05,
            fill_time=cycle_time,
            status="simulated",
            reason="Entry: replay",
            metadata={},
        )
        context = BotContext(
            data={
                "config": ScalpingConfig(),
                "cycle_timestamp": cycle_time.isoformat(),
                "option_chains": {
                    symbol: _OptionChain(
                        options=[_OptionQuote(strike=74200, option_type="PE", ltp=14.55, bid=14.50, spread_pct=0.2)]
                    )
                },
            }
        )

        manager = PositionManagerAgent()
        manager._create_position(order, context)
        flatten_orders = manager.flatten_open_positions(context, reason="Replay completed")

        self.assertEqual(len(flatten_orders), 1)
        trade = next(iter(manager._trade_records.values()))
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_time"], cycle_time.isoformat())
        self.assertAlmostEqual(trade["realized_pnl"], -11.0, places=2)
        self.assertEqual(context.data["capital_state"]["unrealized_pnl"], 0.0)

    def test_trade_normalizer_defaults_decision_packet(self):
        normalized = _normalize_trade(
            {
                "trade_id": "POS_1",
                "symbol": "BSE:SENSEX-INDEX",
                "strike": 74200,
                "option_type": "PE",
                "direction": "long",
                "entry_time": "2026-03-18T14:55:03",
                "entry_price": 15.05,
                "quantity": 20,
                "lots": 1,
                "status": "open",
            }
        )

        self.assertEqual(normalized["decision_packet"], {})

    def test_trade_normalizer_calculates_average_exit_price_from_partials(self):
        normalized = _normalize_trade(
            {
                "trade_id": "POS_2",
                "symbol": "BSE:SENSEX-INDEX",
                "strike": 76500,
                "option_type": "CE",
                "direction": "long",
                "entry_time": "2026-03-18T09:37:14",
                "entry_price": 100.0,
                "quantity": 10,
                "status": "closed",
                "exit_time": "2026-03-18T09:39:11",
                "exit_price": 95.0,
                "partial_exits": [
                    {"time": "2026-03-18T09:38:00", "quantity": 6, "price": 110.0, "pnl": 60.0}
                ],
                "realized_pnl": 40.0,
            }
        )

        self.assertEqual(normalized["exit_price"], 95.0)
        self.assertAlmostEqual(normalized["average_exit_price"], 104.0, places=2)


if __name__ == "__main__":
    unittest.main()
