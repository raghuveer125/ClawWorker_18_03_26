import asyncio
import sys
from pathlib import Path
import unittest


SCALPING_ROOT = Path(__file__).resolve().parent
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping.agents.analysis_agents import StrikeSelectorAgent
from scalping.agents.data_agents import OptionChainData, OptionData
from scalping.agents.execution_agents import EntryAgent
from scalping.base import BotContext


class ReplayHistoricalSignalTests(unittest.TestCase):
    def test_replay_strike_selector_uses_journal_approved_rows(self):
        chain = OptionChainData(
            underlying="NSE:NIFTY50-INDEX",
            spot_price=24520.25,
            atm_strike=24500,
            pcr=1.0,
            max_pain=24500,
            total_ce_oi=10000,
            total_pe_oi=12000,
            timestamp="2026-03-06T11:44:35",
            options=[
                OptionData(
                    symbol="NIFTY50-24600PE",
                    strike=24600,
                    option_type="PE",
                    ltp=195.60,
                    bid=194.90,
                    ask=196.30,
                    bid_qty=1200,
                    ask_qty=1200,
                    volume=18650,
                    oi=98400,
                    oi_change=1200,
                    delta=-0.43,
                    gamma=0.01,
                    theta=-5.0,
                    vega=2.1,
                    iv=14.0,
                    spread=1.4,
                    spread_pct=0.72,
                )
            ],
        )
        context = BotContext(
            data={
                "replay_mode": True,
                "option_chains": {"NSE:NIFTY50-INDEX": chain},
                "spot_data": {},
                "market_structure": {},
                "momentum_signals": [],
                "replay_payload": {
                    "option_rows": {
                        "NSE:NIFTY50-INDEX": [
                            {
                                "symbol": "NIFTY50",
                                "side": "PE",
                                "strike": "24600",
                                "entry": "195.60",
                                "sl": "178.00",
                                "t1": "217.12",
                                "confidence": "95",
                                "status": "APPROVED",
                                "selected": "Y",
                                "entry_ready": "Y",
                                "action": "Take",
                            }
                        ]
                    }
                },
            }
        )

        result = asyncio.run(StrikeSelectorAgent().execute(context))

        self.assertEqual(result.output["total_selections"], 1)
        selections = context.data["strike_selections"]["NSE:NIFTY50-INDEX"]
        self.assertEqual(selections[0].strike, 24600)
        self.assertEqual(selections[0].option_type, "PE")
        self.assertEqual(selections[0].source, "replay_journal")
        self.assertAlmostEqual(selections[0].confidence, 0.95, places=3)

    def test_entry_agent_accepts_replay_journal_approved_signal(self):
        agent = EntryAgent(dry_run=True)
        conditions = agent._augment_replay_entry_conditions(
            {
                "source": "replay_journal",
                "status": "APPROVED",
                "selected": "Y",
                "entry_ready": "Y",
                "action": "Take",
            },
            [],
        )

        self.assertIn("historical_signal_approved", conditions)
        self.assertIn("historical_entry_ready", conditions)
        self.assertGreaterEqual(len(conditions), 2)


if __name__ == "__main__":
    unittest.main()
