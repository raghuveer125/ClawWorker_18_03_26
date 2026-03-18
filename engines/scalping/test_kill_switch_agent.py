from datetime import datetime, timedelta
from pathlib import Path
import sys

SCALPING_ROOT = Path(__file__).resolve().parent
if str(SCALPING_ROOT) not in sys.path:
    sys.path.insert(0, str(SCALPING_ROOT))

from scalping.agents.data_agents import SpotData
from scalping.agents.kill_switch_agent import KillSwitchAgent
from scalping.base import BotContext


def _spot(symbol: str, price: float, timestamp: datetime) -> SpotData:
    return SpotData(
        symbol=symbol,
        ltp=price,
        open=price,
        high=price + 10,
        low=price - 10,
        prev_close=price,
        volume=1000,
        vwap=price,
        change_pct=0.0,
        timestamp=timestamp,
    )


def test_volatility_check_resets_baseline_after_long_gap():
    agent = KillSwitchAgent()
    base_time = datetime(2026, 3, 18, 9, 23, 45)
    context = BotContext(data={"cycle_timestamp": base_time.isoformat()})
    symbol = "NSE:NIFTY50-INDEX"

    first = _spot(symbol, 22000.0, base_time)
    context.data["spot_data"] = {symbol: first}
    triggered, details = agent._check_volatility(context)

    assert triggered is False
    assert details["volatility_ok"] is True
    assert agent._last_prices[symbol] == 22000.0

    resumed_time = base_time + timedelta(minutes=15, seconds=5)
    resumed = _spot(symbol, 22800.0, resumed_time)
    context.data["cycle_timestamp"] = resumed_time.isoformat()
    context.data["spot_data"] = {symbol: resumed}

    triggered, details = agent._check_volatility(context)

    assert triggered is False
    assert details["baseline_resets"] == [{
        "symbol": symbol,
        "gap_seconds": 905.0,
        "reset_threshold_seconds": agent.DATA_STALENESS_SECONDS,
    }]
    assert agent._last_prices[symbol] == 22800.0
    assert agent._last_data_times[symbol] == resumed_time


def test_reset_clears_volatility_baseline():
    agent = KillSwitchAgent()
    now = datetime(2026, 3, 18, 9, 23, 45)
    agent._last_prices["NSE:NIFTY50-INDEX"] = 22000.0
    agent._last_data_times["NSE:NIFTY50-INDEX"] = now
    agent._atr_history["NSE:NIFTY50-INDEX"] = [10.0, 12.0]

    agent._reset_kill_switch("auto_reset")

    assert agent._last_prices == {}
    assert agent._last_data_times == {}
    assert agent._atr_history == {}
