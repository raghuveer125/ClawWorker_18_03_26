#!/usr/bin/env python3
"""
Scalping System Launcher - Market-hours aware execution.

Features:
- Auto-start at market open (9:15 AM IST)
- Auto-stop at market close (3:30 PM IST)
- Pre-market warmup (load data, check connections)
- Post-market learning run
- Graceful shutdown handling

Usage:
    # Run manually
    python launcher.py

    # Run with custom settings
    python launcher.py --live --interval 3

    # Schedule with cron (add to crontab -e):
    # 0 9 * * 1-5 cd /path/to/bot_army && /path/to/python scalping/launcher.py >> logs/scalping.log 2>&1

    # Or use launchd on macOS (see generated plist file)
"""

import asyncio
import argparse
import signal
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional
import os

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scalping.engine import ScalpingEngine
from scalping.config import ScalpingConfig


# Indian market hours (IST)
MARKET_OPEN = time(9, 15)       # 9:15 AM
MARKET_CLOSE = time(15, 30)     # 3:30 PM
PRE_MARKET_START = time(8, 58)  # 8:58 AM - warmup (user requested)
POST_MARKET_END = time(15, 40)  # 3:40 PM - learning run (user requested)


class MarketHoursLauncher:
    """
    Launches scalping engine respecting market hours.
    """

    def __init__(
        self,
        dry_run: bool = True,
        interval: float = 5.0,
        respect_hours: bool = True,
        auto_learning: bool = True,
    ):
        self.dry_run = dry_run
        self.interval = interval
        self.respect_hours = respect_hours
        self.auto_learning = auto_learning

        self.engine: Optional[ScalpingEngine] = None
        self.running = False
        self.shutdown_requested = False

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Shutdown signal received...")
        self.shutdown_requested = True
        self.running = False

    def _get_current_time(self) -> time:
        """Get current time (IST assumed)."""
        return datetime.now().time()

    def _is_trading_day(self) -> bool:
        """Check if today is a trading day (Mon-Fri)."""
        # 0 = Monday, 6 = Sunday
        return datetime.now().weekday() < 5

    def _is_market_hours(self) -> bool:
        """Check if current time is within market hours."""
        if not self.respect_hours:
            return True

        if not self._is_trading_day():
            return False

        current = self._get_current_time()
        return MARKET_OPEN <= current <= MARKET_CLOSE

    def _is_pre_market(self) -> bool:
        """Check if we're in pre-market warmup period."""
        if not self._is_trading_day():
            return False

        current = self._get_current_time()
        return PRE_MARKET_START <= current < MARKET_OPEN

    def _is_post_market(self) -> bool:
        """Check if we're in post-market period."""
        if not self._is_trading_day():
            return False

        current = self._get_current_time()
        return MARKET_CLOSE < current <= POST_MARKET_END

    def _seconds_until_market_open(self) -> float:
        """Calculate seconds until market opens."""
        now = datetime.now()
        today_open = datetime.combine(now.date(), MARKET_OPEN)

        if now.time() >= MARKET_OPEN:
            # Already past open today, calculate for tomorrow
            tomorrow = now.date() + timedelta(days=1)
            # Skip weekends
            while tomorrow.weekday() >= 5:
                tomorrow += timedelta(days=1)
            today_open = datetime.combine(tomorrow, MARKET_OPEN)

        return (today_open - now).total_seconds()

    async def _warmup(self):
        """Pre-market warmup - initialize connections, load data."""
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Pre-market warmup...")

        # Initialize engine
        config = ScalpingConfig()
        self.engine = ScalpingEngine(config=config, dry_run=self.dry_run)

        # Start event bus
        await self.engine.start()

        # Run a single cycle to warm up caches
        print("  - Running warmup cycle...")
        await self.engine.run_cycle()

        print("  - Warmup complete. Waiting for market open...")

    async def _run_learning(self):
        """Post-market learning run."""
        if not self.auto_learning:
            return

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Post-market learning run...")

        if self.engine:
            # Force learning cycle
            self.engine.last_learning_run = None
            context = self.engine.context
            if context:
                # Run learning agents
                print("  - Running QuantLearner...")
                await self.engine.quant_learner.run(context)

                print("  - Running StrategyOptimizer...")
                await self.engine.strategy_optimizer.run(context)

        print("  - Learning complete.")

    async def run(self):
        """Main run loop."""
        self.running = True

        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║              SCALPING SYSTEM LAUNCHER                            ║
╠══════════════════════════════════════════════════════════════════╣
║  Mode: {'DRY RUN' if self.dry_run else 'LIVE TRADING'}                                           ║
║  Interval: {self.interval}s                                              ║
║  Respect Hours: {self.respect_hours}                                       ║
║  Market: 9:15 AM - 3:30 PM IST                                   ║
╚══════════════════════════════════════════════════════════════════╝
        """)

        try:
            while self.running and not self.shutdown_requested:
                current = self._get_current_time()

                # Pre-market warmup
                if self._is_pre_market() and self.engine is None:
                    await self._warmup()
                    await asyncio.sleep(10)
                    continue

                # Market hours - run trading
                if self._is_market_hours():
                    if self.engine is None:
                        # Late start - initialize now
                        config = ScalpingConfig()
                        self.engine = ScalpingEngine(config=config, dry_run=self.dry_run)
                        await self.engine.start()

                    # Run trading cycle
                    await self.engine.run_cycle()
                    await asyncio.sleep(self.interval)

                # Post-market learning
                elif self._is_post_market():
                    await self._run_learning()
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Market closed. Shutting down.")
                    break

                # Outside market hours
                else:
                    if not self.respect_hours:
                        # Run anyway if hours check disabled
                        if self.engine is None:
                            config = ScalpingConfig()
                            self.engine = ScalpingEngine(config=config, dry_run=self.dry_run)
                            await self.engine.start()
                        await self.engine.run_cycle()
                        await asyncio.sleep(self.interval)
                    else:
                        # Wait for market
                        wait_secs = self._seconds_until_market_open()
                        if wait_secs > 0:
                            hours = int(wait_secs // 3600)
                            mins = int((wait_secs % 3600) // 60)
                            print(f"[{current.strftime('%H:%M:%S')}] Market closed. Opens in {hours}h {mins}m")

                            # Sleep in chunks to allow shutdown
                            sleep_chunk = min(300, wait_secs)  # 5 min chunks
                            await asyncio.sleep(sleep_chunk)

        except asyncio.CancelledError:
            print("\nTask cancelled.")

        finally:
            # Cleanup
            if self.engine:
                await self.engine.stop()

            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Launcher stopped.")


def generate_launchd_plist():
    """Generate macOS launchd plist for auto-start."""
    script_path = Path(__file__).resolve()
    python_path = sys.executable
    log_path = script_path.parent.parent / "logs" / "scalping.log"

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.botarmy.scalping</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
        <string>--respect-hours</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{script_path.parent.parent}</string>

    <key>StartCalendarInterval</key>
    <array>
        <!-- Monday to Friday at 9:00 AM -->
        <dict>
            <key>Weekday</key><integer>1</integer>
            <key>Hour</key><integer>9</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>2</integer>
            <key>Hour</key><integer>9</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>3</integer>
            <key>Hour</key><integer>9</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>4</integer>
            <key>Hour</key><integer>9</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>5</integer>
            <key>Hour</key><integer>9</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
    </array>

    <key>StandardOutPath</key>
    <string>{log_path}</string>

    <key>StandardErrorPath</key>
    <string>{log_path}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""

    plist_path = Path.home() / "Library/LaunchAgents/com.botarmy.scalping.plist"
    plist_path.write_text(plist)
    print(f"Generated: {plist_path}")
    print(f"\nTo enable:")
    print(f"  launchctl load {plist_path}")
    print(f"\nTo disable:")
    print(f"  launchctl unload {plist_path}")


def generate_cron_entry():
    """Generate cron entry for scheduling."""
    script_path = Path(__file__).resolve()
    python_path = sys.executable
    log_path = script_path.parent.parent / "logs" / "scalping.log"

    print(f"""
# Add to crontab with: crontab -e
# Runs Mon-Fri at 9:00 AM

0 9 * * 1-5 cd {script_path.parent.parent} && {python_path} {script_path} --respect-hours >> {log_path} 2>&1
""")


async def main():
    parser = argparse.ArgumentParser(description="Scalping System Launcher")
    parser.add_argument("--live", action="store_true",
                        help="Run in LIVE mode (real orders)")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Cycle interval in seconds")
    parser.add_argument("--no-respect-hours", action="store_true",
                        help="Ignore market hours (run anytime)")
    parser.add_argument("--respect-hours", action="store_true",
                        help="Only run during market hours (default)")
    parser.add_argument("--no-learning", action="store_true",
                        help="Disable post-market learning")
    parser.add_argument("--generate-launchd", action="store_true",
                        help="Generate macOS launchd plist")
    parser.add_argument("--generate-cron", action="store_true",
                        help="Show cron entry for scheduling")

    args = parser.parse_args()

    if args.generate_launchd:
        generate_launchd_plist()
        return

    if args.generate_cron:
        generate_cron_entry()
        return

    # Create logs directory
    logs_dir = Path(__file__).parent.parent / "logs"
    logs_dir.mkdir(exist_ok=True)

    launcher = MarketHoursLauncher(
        dry_run=not args.live,
        interval=args.interval,
        respect_hours=not args.no_respect_hours,
        auto_learning=not args.no_learning,
    )

    await launcher.run()


if __name__ == "__main__":
    asyncio.run(main())
