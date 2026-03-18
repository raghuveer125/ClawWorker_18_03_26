import unittest
from pathlib import Path


CLAWWORK_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = CLAWWORK_ROOT.parent


class PaperTradingStartupScriptsTests(unittest.TestCase):
    def test_default_startup_scripts_do_not_launch_legacy_runner(self):
        launcher_text = (PROJECT_ROOT / "shared_project_engine" / "launcher" / "start.sh").read_text(encoding="utf-8")
        clawwork_text = (CLAWWORK_ROOT / "start_paper_trading.sh").read_text(encoding="utf-8")

        self.assertNotIn("paper_trading_runner.py", launcher_text)
        self.assertNotIn("paper_trading_runner.py", clawwork_text)

    def test_default_startup_scripts_call_out_autotrader_as_paper_engine(self):
        launcher_text = (PROJECT_ROOT / "shared_project_engine" / "launcher" / "start.sh").read_text(encoding="utf-8")
        clawwork_text = (CLAWWORK_ROOT / "start_paper_trading.sh").read_text(encoding="utf-8")

        self.assertIn("AutoTrader", launcher_text)
        self.assertIn("AutoTrader", clawwork_text)


if __name__ == "__main__":
    unittest.main()
