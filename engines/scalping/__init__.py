"""
Bot Army - Hedge Fund Style Bot Architecture

A modular, event-driven system for automated trading and code quality.

Components:
- bots/: Individual bot implementations (guardian, backtest, risk, etc.)
- orchestrator/: Pipeline and event management
- knowledge/: Trade memory and learning
- pipelines/: YAML pipeline definitions

Usage:
    python -m bot_army.main run --pipeline trade_cycle
    python -m bot_army.main list
    python -m bot_army.main status
"""

__version__ = "0.1.0"
