"""Main runner — starts dry-run or replay mode."""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

from ..config import ScalpingConfig
from ..risk_engine import KillSwitch
from .kafka_config import get_bus
from .position_manager import PositionManager
from .execution_simulator import ExecutionSimulator
from .entry_engine import EntryEngine
from .exit_engine import ExitEngine
from .market_data_producer import MarketDataProducer
from .signal_producer import SignalProducer
from .replay_engine import ReplayEngine
from .logger_service import LoggerService
from .metrics import MetricsEngine


class DryRunOrchestrator:
    """Orchestrates the full dry-run trading pipeline."""

    def __init__(self, config: ScalpingConfig, replay_file: str = "", replay_speed: float = 1.0) -> None:
        self.config = config
        self.pm = PositionManager()
        self.executor = ExecutionSimulator(self.pm)
        self.entry_engine = EntryEngine(self.executor, config)
        self.exit_engine = ExitEngine(self.executor, self.pm, config)
        self.market_producer = MarketDataProducer()
        self.signal_producer = SignalProducer()
        self.metrics = MetricsEngine(self.pm)
        self.logger = LoggerService()
        self.kill_switch = KillSwitch()
        self.replay_file = replay_file
        self.replay_speed = replay_speed
        self._cycle_count = 0
        self._start_time = datetime.now()

    def run_synthetic(self, duration_seconds: int = 300, cycle_ms: int = 300) -> None:
        """Run synthetic dry-run with generated market data."""
        print(f"\n{'='*60}")
        print(f"  DRY-RUN MODE: Synthetic Market Data")
        print(f"  Duration: {duration_seconds}s | Cycle: {cycle_ms}ms")
        print(f"  Capital: ₹{self.config.total_capital:,.0f}")
        print(f"{'='*60}\n")

        base_prices = {
            "NSE:NIFTY50-INDEX": 22800.0,
            "NSE:NIFTYBANK-INDEX": 51500.0,
        }
        option_configs = [
            {"underlying": "NSE:NIFTY50-INDEX", "strike": 22000, "type": "PE", "base_premium": 65.0},
            {"underlying": "NSE:NIFTY50-INDEX", "strike": 22050, "type": "PE", "base_premium": 72.0},
            {"underlying": "NSE:NIFTY50-INDEX", "strike": 22100, "type": "PE", "base_premium": 80.0},
        ]

        end_time = self._start_time + timedelta(seconds=duration_seconds)
        sim_time = datetime(2026, 4, 2, 10, 0, 0)
        last_summary = time.time()

        while datetime.now() < end_time:
            self._cycle_count += 1
            sim_time += timedelta(milliseconds=cycle_ms * self.replay_speed)

            # Check kill switch
            ctx = self._build_context(sim_time)
            halt, reason = self.kill_switch.check(ctx, self.config)
            if halt:
                print(f"[Cycle {self._cycle_count}] KILL SWITCH: {reason}")
                break

            # Generate spot ticks
            vix = 15.0 + random.gauss(0, 0.5)
            for symbol, base in base_prices.items():
                price = self.market_producer.generate_synthetic_tick(symbol, base, volatility=0.0003)
                self.market_producer.publish_tick(symbol, price, vix=vix, timestamp=sim_time)

            # Generate option ticks and signals
            signals = []
            for opt in option_configs:
                premium = self.market_producer.generate_synthetic_tick(
                    f"{opt['underlying']}|{opt['strike']}|{opt['type']}",
                    opt["base_premium"],
                    volatility=0.003,
                )
                spread = round(random.uniform(0.10, 0.30), 2)
                bid = round(premium - spread / 2, 2)
                ask = round(premium + spread / 2, 2)

                self.market_producer.publish_option_tick(
                    underlying=opt["underlying"],
                    strike=opt["strike"],
                    option_type=opt["type"],
                    ltp=premium,
                    bid=bid,
                    ask=ask,
                    volume=random.randint(5000, 50000),
                    oi=random.randint(10000, 100000),
                    timestamp=sim_time,
                )

                tick = {
                    "event": "option_tick",
                    "underlying": opt["underlying"],
                    "strike": opt["strike"],
                    "option_type": opt["type"],
                    "ltp": premium,
                    "bid": bid,
                    "ask": ask,
                    "spread_pct": round(spread / premium * 100, 2),
                    "volume": random.randint(5000, 50000),
                    "oi": random.randint(10000, 100000),
                    "delta": 0.20,
                }
                signal = self.signal_producer.on_tick(tick)
                if signal:
                    signals.append(signal)

            ctx = self._build_context(sim_time, vix=vix)

            # Process entries
            if signals:
                self.entry_engine.evaluate_signals(signals, ctx)

            # Process pending partial fills
            self.executor.process_pending_orders(sim_time)

            # Process exits
            price_map = {}
            for pos in self.pm.get_open_positions():
                key = f"{pos.symbol}|{pos.strike}|{pos.option_type}"
                ltp = self.market_producer._last_prices.get(key, pos.current_price)
                price_map[pos.position_id] = ltp
            self.exit_engine.check_exits(price_map, ctx)

            # Snapshot P&L
            self.metrics.snapshot_pnl()

            # Print summary every 60s
            if time.time() - last_summary >= 60:
                self.metrics.print_summary()
                last_summary = time.time()

            time.sleep(cycle_ms / 1000)

        self._finalize()

    def run_replay(self) -> None:
        """Run replay from historical file."""
        if not self.replay_file:
            print("ERROR: No replay file specified")
            return

        replay = ReplayEngine(speed=self.replay_speed)
        if self.replay_file.endswith(".csv"):
            count = replay.load_csv(self.replay_file)
        else:
            count = replay.load_json(self.replay_file)

        print(f"\n{'='*60}")
        print(f"  REPLAY MODE: {self.replay_file}")
        print(f"  Ticks: {count} | Speed: {self.replay_speed}x")
        print(f"  Capital: ₹{self.config.total_capital:,.0f}")
        print(f"{'='*60}\n")

        sim_time = datetime(2026, 4, 2, 9, 30, 0)
        last_summary = time.time()

        while True:
            tick = replay.step()
            if tick is None:
                break

            self._cycle_count += 1
            ts_str = tick.get("timestamp", "")
            if ts_str:
                try:
                    sim_time = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                except ValueError:
                    sim_time += timedelta(milliseconds=300)
            else:
                sim_time += timedelta(milliseconds=300)

            vix = float(tick.get("vix", 15) or 15)
            ctx = self._build_context(sim_time, vix=vix)

            # Publish tick
            if tick.get("strike"):
                signal = self.signal_producer.on_tick({
                    "event": "option_tick",
                    "underlying": tick.get("symbol", ""),
                    **tick,
                })
                if signal:
                    self.entry_engine.evaluate_signals([signal], ctx)

            # Process exits
            price_map = {}
            for pos in self.pm.get_open_positions():
                key = f"{pos.symbol}|{pos.strike}|{pos.option_type}"
                price_map[pos.position_id] = float(tick.get("ltp", pos.current_price) or pos.current_price)
            self.exit_engine.check_exits(price_map, ctx)

            self.executor.process_pending_orders(sim_time)
            self.metrics.snapshot_pnl()

            if time.time() - last_summary >= 30:
                pct = replay.progress_pct
                print(f"[Replay {pct:.0f}%] Cycle {self._cycle_count} | "
                      f"P&L: ₹{self.pm.daily_pnl:.2f} | "
                      f"Open: {len(self.pm.get_open_positions())} | "
                      f"Closed: {len(self.pm.get_all_closed())}")
                last_summary = time.time()

        self._finalize()

    def _build_context(self, sim_time: datetime, vix: float = 15.0) -> Dict[str, Any]:
        # Build synthetic structure breaks and momentum for signal validation
        M = type("M", (), {
            "symbol": "NSE:NIFTY50-INDEX", "signal_type": "futures_surge",
            "strength": 0.85, "price_move": -35, "direction": "bearish",
        })
        B = type("B", (), {
            "symbol": "NSE:NIFTY50-INDEX", "break_type": "bos_bearish",
        })
        return {
            "vix": vix,
            "daily_pnl": self.pm.daily_pnl,
            "unrealized_pnl": self.pm.total_unrealized_pnl,
            "positions": self.pm.get_open_positions(),
            "cycle_now": sim_time,
            "spot_timestamp": (sim_time - timedelta(seconds=1)).isoformat(),
            "momentum_signals": [M()],
            "structure_breaks": [B()],
            "executed_trades": [
                {"exit_time": p.exit_time.isoformat() if p.exit_time else None, "realized_pnl": p.realized_pnl}
                for p in self.pm.get_all_closed()
            ],
        }

    def _finalize(self) -> None:
        self.logger.flush()
        elapsed = (datetime.now() - self._start_time).total_seconds()
        print(f"\n[DryRun] Completed {self._cycle_count} cycles in {elapsed:.1f}s")
        self.metrics.print_summary()
        print(f"Entry stats: {self.entry_engine.stats}")
        print(f"Exit stats: {self.exit_engine.stats}")


def main():
    parser = argparse.ArgumentParser(description="Dry-Run / Replay Trading System")
    parser.add_argument("--mode", choices=["synthetic", "replay"], default="synthetic")
    parser.add_argument("--file", type=str, default="", help="Replay file (JSON or CSV)")
    parser.add_argument("--speed", type=float, default=1.0, help="Replay speed multiplier")
    parser.add_argument("--duration", type=int, default=300, help="Synthetic run duration (seconds)")
    parser.add_argument("--cycle-ms", type=int, default=300, help="Cycle interval (ms)")
    parser.add_argument("--capital", type=float, default=100000, help="Starting capital")
    args = parser.parse_args()

    config = ScalpingConfig(total_capital=args.capital)
    orchestrator = DryRunOrchestrator(config, replay_file=args.file, replay_speed=args.speed)

    if args.mode == "replay" and args.file:
        orchestrator.run_replay()
    else:
        orchestrator.run_synthetic(duration_seconds=args.duration, cycle_ms=args.cycle_ms)


if __name__ == "__main__":
    main()
