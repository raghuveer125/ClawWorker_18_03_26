"""Paper trading module — simulated execution, capital management, and trade journal."""

from .broker import PaperBroker
from .capital_manager import CapitalManager

__all__ = ["PaperBroker", "CapitalManager"]
