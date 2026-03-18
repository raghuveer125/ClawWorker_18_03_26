import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import unittest


SCALPING_ROOT = Path(__file__).resolve().parent
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping.agents.execution_agents import (
    EntryAgent,
    EntrySignal,
    ExitAgent,
    Order,
    Position,
    PositionManagerAgent,
    _round_to_tick,
)
from scalping.base import BotContext
from scalping.config import ScalpingConfig


@dataclass
class _OptionQuote:
    strike: int
    option_type: str
    ltp: float
    bid: float = 0.0


@dataclass
class _OptionChain:
    options: list


class ExecutionTickSizeTests(unittest.TestCase):
    def test_round_to_tick_snaps_prices_to_option_ticks(self):
        self.assertAlmostEqual(_round_to_tick(584.78, 0.05), 584.80, places=2)
        self.assertAlmostEqual(_round_to_tick(584.74, 0.05), 584.75, places=2)

    def test_entry_and_position_prices_are_tick_normalized(self):
        agent = EntryAgent(dry_run=True)
        signal = EntrySignal(
            symbol="BSE:SENSEX-INDEX",
            direction="CE",
            strike=73500,
            premium=584.78,
            lots=1,
            confidence=0.9,
            conditions_met=["historical_signal_approved", "historical_entry_ready"],
            timestamp=datetime.now(),
        )

        order = asyncio.run(
            agent._create_entry_order(
                signal,
                signal.symbol,
                ScalpingConfig(),
                multiplier=1.0,
                replay_mode=True,
                metadata={"ask": 584.78},
            )
        )

        manager = PositionManagerAgent()
        manager._create_position(order, BotContext(data={"config": ScalpingConfig()}))
        position = next(iter(manager._positions.values()))

        self.assertAlmostEqual(order.price, 584.80, places=2)
        self.assertAlmostEqual(order.fill_price, 584.80, places=2)
        self.assertAlmostEqual(position.entry_price, 584.80, places=2)
        self.assertAlmostEqual(position.sl_price * 20, round(position.sl_price * 20), places=6)
        self.assertAlmostEqual(position.target_price * 20, round(position.target_price * 20), places=6)

    def test_exit_fill_and_mark_price_are_tick_normalized(self):
        exit_agent = ExitAgent(dry_run=True)
        order = Order(
            order_id="PART_demo",
            symbol="BSE:SENSEX-INDEX",
            strike=73500,
            option_type="CE",
            order_type="market",
            side="sell",
            quantity=10,
            price=584.78,
            status="simulated",
        )
        chains = {
            "BSE:SENSEX-INDEX": _OptionChain(
                options=[_OptionQuote(strike=73500, option_type="CE", ltp=584.78, bid=584.74)]
            )
        }

        exit_agent._simulate_exit_fill(order, chains)
        current_price = exit_agent._get_current_price(
            Position(
                position_id="POS_demo",
                symbol="BSE:SENSEX-INDEX",
                strike=73500,
                option_type="CE",
                entry_price=584.80,
                entry_time=datetime.now(),
                quantity=10,
                lots=1,
                lot_size=10,
                direction="long",
                status="open",
            ),
            chains,
        )

        self.assertAlmostEqual(order.fill_price, 584.75, places=2)
        self.assertAlmostEqual(current_price, 584.80, places=2)


if __name__ == "__main__":
    unittest.main()
