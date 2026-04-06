"""Unit tests for paper trading — broker, capital manager, position sizing."""

import pytest
from dataclasses import replace
from engines.lottery.config import load_config, SizingMode
from engines.lottery.models import OptionType, ExitReason, TradeStatus
from engines.lottery.paper_trading import PaperBroker, CapitalManager
from engines.lottery.calculations.scoring import ScoredCandidate


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def candidate():
    return ScoredCandidate(
        strike=24000, option_type=OptionType.CE, ltp=3.50, score=42.5,
        components={}, band_fit=0.8, spread_pct=1.2, volume=5000000,
        distance=1300, source="VISIBLE",
    )


class TestPaperBroker:

    def test_entry_ltp_mode(self, cfg, candidate):
        from engines.lottery.config import ExecutionMode
        cfg_ltp = replace(cfg, execution=replace(cfg.execution, mode=ExecutionMode.LTP))
        broker = PaperBroker(config=cfg_ltp)
        trade = broker.execute_entry(
            candidate, "NIFTY", "2026-04-07", 75, 1, 100000,
            "sig1", "snap1", "v1",
        )
        assert trade.entry_price == 3.50
        assert trade.status == TradeStatus.OPEN

    def test_entry_mid_slippage(self, cfg, candidate):
        broker = PaperBroker(config=cfg)
        trade = broker.execute_entry(
            candidate, "NIFTY", "2026-04-07", 75, 1, 100000,
            "sig1", "snap1", "v1", bid=3.40, ask=3.60,
        )
        # MID = 3.50, + 0.5% slippage = 3.5175
        assert trade.entry_price == pytest.approx(3.52, abs=0.01)

    def test_exit_pnl_profit(self, cfg, candidate):
        broker = PaperBroker(config=cfg)
        trade = broker.execute_entry(
            candidate, "NIFTY", "2026-04-07", 75, 1, 100000,
            "sig1", "snap1", "v1",
        )
        closed = broker.execute_exit(trade, 10.0, ExitReason.TARGET_2, 1)
        assert closed.status == TradeStatus.CLOSED
        assert closed.pnl > 0
        assert closed.exit_price > 0
        assert closed.reason_exit == ExitReason.TARGET_2

    def test_exit_pnl_loss(self, cfg, candidate):
        broker = PaperBroker(config=cfg)
        trade = broker.execute_entry(
            candidate, "NIFTY", "2026-04-07", 75, 1, 100000,
            "sig1", "snap1", "v1",
        )
        closed = broker.execute_exit(trade, 1.0, ExitReason.STOP_LOSS, 1)
        assert closed.pnl < 0

    def test_exit_levels(self, cfg, candidate):
        broker = PaperBroker(config=cfg)
        trade = broker.execute_entry(
            candidate, "NIFTY", "2026-04-07", 75, 1, 100000,
            "sig1", "snap1", "v1",
        )
        assert trade.sl == pytest.approx(trade.entry_price * 0.5, abs=0.01)
        assert trade.t1 == pytest.approx(trade.entry_price * 2.0, abs=0.01)
        assert trade.t2 == pytest.approx(trade.entry_price * 3.0, abs=0.01)
        assert trade.t3 == pytest.approx(trade.entry_price * 4.0, abs=0.01)

    def test_charges_computed(self, cfg, candidate):
        broker = PaperBroker(config=cfg)
        trade = broker.execute_entry(
            candidate, "NIFTY", "2026-04-07", 75, 1, 100000,
            "sig1", "snap1", "v1",
        )
        assert trade.charges > 0


class TestCapitalManager:

    def test_initial_capital(self, cfg):
        cm = CapitalManager(config=cfg, symbol="NIFTY")
        assert cm.running_capital == cfg.paper_trading.starting_capital
        assert cm.realized_pnl == 0.0
        assert cm.drawdown == 0.0

    def test_fixed_lot_sizing(self, cfg):
        cm = CapitalManager(config=cfg, symbol="NIFTY")
        qty, lots = cm.compute_position_size(3.50, 75)
        assert lots == cfg.paper_trading.fixed_lots
        assert qty == lots * 75

    def test_record_exit_profit(self, cfg, candidate):
        cm = CapitalManager(config=cfg, symbol="NIFTY")
        broker = PaperBroker(config=cfg)
        trade = broker.execute_entry(
            candidate, "NIFTY", "2026-04-07", 75, 1, cm.running_capital,
            "sig1", "snap1", "v1",
        )
        cm.record_entry(trade)
        closed = broker.execute_exit(trade, 10.0, ExitReason.TARGET_2, 1)
        cm.record_exit(closed)
        assert cm.realized_pnl > 0
        assert cm.running_capital > cfg.paper_trading.starting_capital

    def test_record_exit_loss(self, cfg, candidate):
        cm = CapitalManager(config=cfg, symbol="NIFTY")
        broker = PaperBroker(config=cfg)
        trade = broker.execute_entry(
            candidate, "NIFTY", "2026-04-07", 75, 1, cm.running_capital,
            "sig1", "snap1", "v1",
        )
        cm.record_entry(trade)
        closed = broker.execute_exit(trade, 1.0, ExitReason.STOP_LOSS, 1)
        cm.record_exit(closed)
        assert cm.realized_pnl < 0
        assert cm.drawdown > 0

    def test_drawdown_tracking(self, cfg, candidate):
        cm = CapitalManager(config=cfg, symbol="NIFTY")
        broker = PaperBroker(config=cfg)
        # Win trade
        t1 = broker.execute_entry(candidate, "NIFTY", "2026-04-07", 75, 1, cm.running_capital, "s1", "sn1", "v1")
        cm.record_entry(t1)
        c1 = broker.execute_exit(t1, 10.0, ExitReason.TARGET_2, 1)
        cm.record_exit(c1)
        peak = cm.peak_capital
        # Loss trade
        t2 = broker.execute_entry(candidate, "NIFTY", "2026-04-07", 75, 1, cm.running_capital, "s2", "sn2", "v1")
        cm.record_entry(t2)
        c2 = broker.execute_exit(t2, 1.0, ExitReason.STOP_LOSS, 1)
        cm.record_exit(c2)
        assert cm.peak_capital == peak
        assert cm.drawdown > 0

    def test_can_trade_check(self, cfg):
        cm = CapitalManager(config=cfg, symbol="NIFTY")
        allowed, reason = cm.can_trade()
        assert allowed

    def test_can_trade_blocked_zero_capital(self, cfg):
        cm = CapitalManager(config=cfg, symbol="NIFTY")
        cm._running_capital = 0
        allowed, reason = cm.can_trade()
        assert not allowed

    def test_ledger_entries(self, cfg, candidate):
        cm = CapitalManager(config=cfg, symbol="NIFTY")
        assert len(cm.ledger) == 1  # INIT entry
        broker = PaperBroker(config=cfg)
        trade = broker.execute_entry(candidate, "NIFTY", "2026-04-07", 75, 1, cm.running_capital, "s1", "sn1", "v1")
        cm.record_entry(trade)
        assert len(cm.ledger) == 2
        closed = broker.execute_exit(trade, 10.0, ExitReason.TARGET_2, 1)
        cm.record_exit(closed)
        assert len(cm.ledger) == 3

    def test_summary(self, cfg):
        cm = CapitalManager(config=cfg, symbol="NIFTY")
        s = cm.get_summary()
        assert s["starting_capital"] == cfg.paper_trading.starting_capital
        assert s["running_capital"] == cfg.paper_trading.starting_capital
        assert s["drawdown"] == 0.0
