import asyncio
import sys
from pathlib import Path
import unittest


SCALPING_ROOT = Path(__file__).resolve().parent
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping.agents.analysis_agents import StrikeSelection
from scalping.agents.signal_quality_agent import SignalQualityAgent
from scalping.base import BotContext
from scalping.config import ScalpingConfig


class ReplaySignalQualityTests(unittest.TestCase):
    def test_signal_quality_preserves_replay_metadata_from_strike_selection(self):
        selection = StrikeSelection(
            symbol="NSE:NIFTY50-INDEX",
            strike=24600,
            option_type="PE",
            premium=195.6,
            delta=-0.43,
            spread=1.4,
            spread_pct=0.72,
            volume=18650,
            oi=98400,
            score=0.95,
            reasons=["Replay journal candidate", "Journal approved"],
            confidence=0.95,
            entry=195.6,
            sl=178.0,
            t1=217.12,
            status="APPROVED",
            action="Take",
            source="replay_journal",
        )
        context = BotContext(
            data={
                "strike_selections": {"NSE:NIFTY50-INDEX": [selection]},
                "market_regimes": {},
                "volume_data": {},
                "liquidity_metrics": {},
                "momentum_signals": [],
                "volatility_surface": {},
                "dealer_pressure": {},
            }
        )

        asyncio.run(SignalQualityAgent().execute(context))

        passed = context.data["quality_filtered_signals"][0]
        self.assertEqual(passed["status"], "APPROVED")
        self.assertEqual(passed["action"], "Take")
        self.assertEqual(passed["source"], "replay_journal")
        self.assertAlmostEqual(passed["confidence"], 0.95, places=3)
        self.assertAlmostEqual(passed["sl"], 178.0, places=3)
        self.assertAlmostEqual(passed["t1"], 217.12, places=3)

    def test_signal_quality_rejects_replay_signal_with_poor_risk_reward(self):
        selection = StrikeSelection(
            symbol="BSE:SENSEX-INDEX",
            strike=76500,
            option_type="CE",
            premium=368.91,
            delta=0.48,
            spread=0.05,
            spread_pct=0.02,
            volume=10331318,
            oi=1806861,
            score=0.95,
            reasons=["Replay journal candidate", "Journal approved"],
            confidence=0.95,
            entry=368.91,
            sl=329.65,
            t1=402.10,
            status="APPROVED",
            action="Take",
            source="replay_journal",
        )
        context = BotContext(
            data={
                "config": ScalpingConfig(replay_min_rr_ratio=1.0),
                "strike_selections": {"BSE:SENSEX-INDEX": [selection]},
                "market_regimes": {},
                "volume_data": {},
                "liquidity_metrics": {"BSE:SENSEX-INDEX": {"liquidity_score": 1.0, "spread_pct": 0.02}},
                "momentum_signals": [],
                "volatility_surface": {},
                "dealer_pressure": {},
            }
        )

        result = asyncio.run(SignalQualityAgent().execute(context))

        self.assertEqual(result.output["signals_passed"], 0)
        self.assertEqual(len(context.data["rejected_signals"]), 1)
        reasons = context.data["rejected_signals"][0]["rejection_reasons"]
        self.assertTrue(any("Replay R:R 0.8 below minimum 1.0" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
