"""Telegram alert notifier for the Lottery pipeline.

Sends notifications on key pipeline events:
- Trade entry
- Trade exit (with PnL)
- Candidate found
- Data quality degraded
- System error

Rate-limited to avoid spam. Credentials from environment variables.
Gracefully skips if not configured — never blocks the pipeline.
"""

import logging
import os
import time
import threading
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class AlertNotifier:
    """Telegram alert sender with rate limiting.

    Reads credentials from environment variables:
    - LOTTERY_TELEGRAM_TOKEN
    - LOTTERY_TELEGRAM_CHAT_ID

    Sends are async (fire-and-forget via thread) to never block the pipeline.
    """

    def __init__(
        self,
        token_env: str = "LOTTERY_TELEGRAM_TOKEN",
        chat_id_env: str = "LOTTERY_TELEGRAM_CHAT_ID",
        rate_limit_seconds: float = 30.0,
        enabled: bool = True,
    ) -> None:
        # Try os.environ first, then load from .env file
        self._token = os.environ.get(token_env, "").strip()
        self._chat_id = os.environ.get(chat_id_env, "").strip()

        if not self._token or not self._chat_id:
            self._load_from_env_file(token_env, chat_id_env)
        self._rate_limit = rate_limit_seconds
        self._enabled = enabled
        self._last_send: dict[str, float] = {}  # event_type → last send time

        if self._token and self._chat_id:
            self._configured = True
            logger.info("Telegram alerts configured (chat=%s)", self._chat_id[:6] + "...")
        else:
            self._configured = False
            if enabled:
                logger.warning("Telegram alerts enabled but credentials not set — alerts will be skipped")

    def _load_from_env_file(self, token_env: str, chat_id_env: str) -> None:
        """Try loading credentials from project .env file."""
        from pathlib import Path
        env_candidates = [
            Path(__file__).resolve().parents[3] / ".env",  # project root
            Path(__file__).resolve().parents[2] / ".env",
            Path.cwd() / ".env",
        ]
        for env_path in env_candidates:
            if not env_path.exists():
                continue
            try:
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if key == token_env and not self._token:
                        self._token = val
                    elif key == chat_id_env and not self._chat_id:
                        self._chat_id = val
                if self._token and self._chat_id:
                    return
            except Exception:
                continue

    @property
    def is_configured(self) -> bool:
        return self._configured and self._enabled

    # ── Event Methods ──────────────────────────────────────────────

    def on_trade_entry(
        self,
        symbol: str,
        strike: float,
        side: str,
        entry_price: float,
        sl: float,
        t1: float,
        qty: int,
    ) -> None:
        """Alert on paper trade entry."""
        msg = (
            f"🎯 *TRADE ENTRY*\n"
            f"```\n"
            f"Symbol:  {symbol}\n"
            f"Strike:  {strike:.0f} {side}\n"
            f"Entry:   ₹{entry_price:.2f}\n"
            f"Qty:     {qty}\n"
            f"SL:      ₹{sl:.2f}\n"
            f"T1:      ₹{t1:.2f}\n"
            f"```"
        )
        self._send("trade_entry", msg)

    def on_trade_exit(
        self,
        symbol: str,
        strike: float,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        reason: str,
    ) -> None:
        """Alert on paper trade exit."""
        emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"{emoji} *TRADE EXIT*\n"
            f"```\n"
            f"Symbol:  {symbol}\n"
            f"Strike:  {strike:.0f} {side}\n"
            f"Entry:   ₹{entry_price:.2f}\n"
            f"Exit:    ₹{exit_price:.2f}\n"
            f"PnL:     ₹{pnl:+.2f}\n"
            f"Reason:  {reason}\n"
            f"```"
        )
        self._send("trade_exit", msg)

    def on_candidate_found(
        self,
        symbol: str,
        strike: float,
        side: str,
        premium: float,
        score: float,
    ) -> None:
        """Alert when a new lottery candidate is found."""
        msg = (
            f"🔍 *CANDIDATE FOUND*\n"
            f"```\n"
            f"Symbol:  {symbol}\n"
            f"Strike:  {strike:.0f} {side}\n"
            f"Premium: ₹{premium:.2f}\n"
            f"Score:   {score:.2f}\n"
            f"```"
        )
        self._send("candidate", msg)

    def on_quality_warning(self, symbol: str, quality: str, score: float) -> None:
        """Alert on data quality degradation."""
        msg = (
            f"⚠️ *DATA QUALITY WARNING*\n"
            f"```\n"
            f"Symbol:  {symbol}\n"
            f"Status:  {quality}\n"
            f"Score:   {score:.4f}\n"
            f"```"
        )
        self._send("quality", msg, rate_limit=120.0)  # max once per 2 min

    def on_system_error(self, symbol: str, error: str) -> None:
        """Alert on system error."""
        msg = (
            f"🚨 *SYSTEM ERROR*\n"
            f"```\n"
            f"Symbol:  {symbol}\n"
            f"Error:   {error[:200]}\n"
            f"```"
        )
        self._send("error", msg, rate_limit=60.0)

    def on_pipeline_start(self, symbol: str, profile: str, dte: int, capital: float) -> None:
        """Alert on pipeline startup."""
        msg = (
            f"🚀 *PIPELINE STARTED*\n"
            f"```\n"
            f"Symbol:  {symbol}\n"
            f"Profile: {profile}\n"
            f"DTE:     {dte}\n"
            f"Capital: ₹{capital:,.0f}\n"
            f"```"
        )
        self._send("startup", msg, rate_limit=0)  # always send

    def on_pipeline_stop(self, symbol: str, trades: int, pnl: float) -> None:
        """Alert on pipeline shutdown."""
        msg = (
            f"🛑 *PIPELINE STOPPED*\n"
            f"```\n"
            f"Symbol:  {symbol}\n"
            f"Trades:  {trades}\n"
            f"PnL:     ₹{pnl:+.2f}\n"
            f"```"
        )
        self._send("shutdown", msg, rate_limit=0)

    def send_custom(self, message: str) -> None:
        """Send a custom message (no rate limiting)."""
        self._send("custom", message, rate_limit=0)

    # ── Core Send Logic ────────────────────────────────────────────

    def _send(self, event_type: str, message: str, rate_limit: Optional[float] = None) -> None:
        """Send a Telegram message with rate limiting.

        Fire-and-forget via thread — never blocks the pipeline.
        """
        if not self.is_configured:
            return

        limit = rate_limit if rate_limit is not None else self._rate_limit
        now = time.monotonic()
        last = self._last_send.get(event_type, 0)

        if limit > 0 and (now - last) < limit:
            logger.debug("Alert rate-limited: %s (%.0fs since last)", event_type, now - last)
            return

        self._last_send[event_type] = now

        # Fire-and-forget in background thread
        thread = threading.Thread(
            target=self._send_telegram,
            args=(message,),
            daemon=True,
            name=f"alert-{event_type}",
        )
        thread.start()

    def _send_telegram(self, message: str) -> None:
        """Actually send the Telegram message (runs in thread)."""
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.debug("Telegram alert sent successfully")
            else:
                logger.warning("Telegram alert failed: %s %s", resp.status_code, resp.text[:100])
        except Exception as e:
            logger.warning("Telegram alert error: %s", e)


# ── Convenience ────────────────────────────────────────────────────────────

def send_test_alert() -> None:
    """Send a test alert to verify Telegram configuration."""
    notifier = AlertNotifier()
    if not notifier.is_configured:
        print("Telegram not configured. Set LOTTERY_TELEGRAM_TOKEN and LOTTERY_TELEGRAM_CHAT_ID")
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    notifier.send_custom(
        f"✅ *Lottery Pipeline Test Alert*\n"
        f"```\n"
        f"Time: {now}\n"
        f"Status: Telegram alerts working!\n"
        f"```"
    )
    print("Test alert sent! Check your Telegram.")
    time.sleep(2)  # wait for thread to complete
