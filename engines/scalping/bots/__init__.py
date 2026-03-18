"""Bot Army - Trading and Code Quality Bots"""

from .base_bot import BaseBot, BotContext, BotResult, BotStatus, CompositeBot
from .guardian_bot import GuardianBot, SelfHealingBot
from .trading_bots import BacktestBot, RegimeBot, RiskSentinelBot, ExecutionBot
from .advanced_bots import (
    MetaStrategyBot,
    CorrelationBot,
    AlphaDecayBot,
    ExperimentBot,
)

__all__ = [
    # Base
    "BaseBot",
    "BotContext",
    "BotResult",
    "BotStatus",
    "CompositeBot",
    # Code Quality
    "GuardianBot",
    "SelfHealingBot",
    # Trading Core
    "BacktestBot",
    "RegimeBot",
    "RiskSentinelBot",
    "ExecutionBot",
    # Advanced (Quant Fund Edge)
    "MetaStrategyBot",
    "CorrelationBot",
    "AlphaDecayBot",
    "ExperimentBot",
]
