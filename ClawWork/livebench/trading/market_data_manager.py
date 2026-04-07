"""
Market Data Manager Mixin — screener snapshot lifecycle, market-data
enhancement, and live price fetching.

Extracted from auto_trader.py to keep the AutoTrader class focused on
orchestration.  AutoTrader inherits from MarketDataMixin and the public
API is unchanged.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MarketDataMixin:
    """Methods for fetching, caching, and enhancing market data.

    Expects the following attributes on ``self`` (set by AutoTrader.__init__):
        screener_dir                        : Path
        screener_max_age_seconds            : int
        screener_refresh_cooldown_seconds   : int
        market_data_status                  : Dict[str, Any]
        price_history                       : Dict[str, List[Dict]]
        _positions_lock                     : threading.Lock
        positions                           : Dict[str, Position]
        mode                                : TradingMode enum
        _last_screener_refresh_attempt      : Optional[datetime]

    Also uses the module-level helper ``_build_fyers_option_symbol`` and the
    ``_get_market_data_client`` method defined on AutoTrader.
    """

    # Index → FYERS symbol mapping for option chain lookups
    _OI_INDEX_SYMBOLS: Dict[str, str] = {
        "NIFTY50": "NSE:NIFTY50-INDEX",
        "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
        "SENSEX": "BSE:SENSEX-INDEX",
    }

    # ------------------------------------------------------------------
    # Market-data status setter
    # ------------------------------------------------------------------

    def _set_market_data_status(
        self,
        *,
        healthy: bool,
        available: bool,
        message: str,
        source: Optional[str] = None,
        file_name: Optional[str] = None,
        updated_at: Optional[str] = None,
        age_seconds: Optional[float] = None,
    ) -> None:
        self.market_data_status = {
            "healthy": healthy,
            "available": available,
            "message": message,
            "source": source,
            "file": file_name,
            "updated_at": updated_at,
            "age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        }

    # ------------------------------------------------------------------
    # Screener snapshot helpers
    # ------------------------------------------------------------------

    def _get_latest_screener_file(self) -> Optional[Path]:
        if not self.screener_dir.exists():
            return None

        screener_files = sorted(
            self.screener_dir.glob("screener_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return screener_files[0] if screener_files else None

    def _read_screener_payload(self, file_path: Path) -> Dict[str, Any]:
        with open(file_path) as f:
            return json.load(f)

    def _write_screener_snapshot(self, payload: Dict[str, Any]) -> Path:
        self.screener_dir.mkdir(parents=True, exist_ok=True)
        out_file = (
            self.screener_dir
            / f"screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        out_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return out_file

    def _refresh_screener_snapshot(
        self,
    ) -> Optional[tuple]:
        """Run the screener directly when the cached snapshot is missing or stale."""
        self._last_screener_refresh_attempt = datetime.now()
        try:
            from .screener import run_screener

            payload = run_screener()
            if not payload.get("success"):
                error = (
                    payload.get("error")
                    or payload.get("message")
                    or "Unknown screener failure"
                )
                self._set_market_data_status(
                    healthy=False,
                    available=False,
                    message=f"Screener refresh failed: {error}",
                    source="live_refresh",
                )
                logger.warning("AutoTrader screener refresh failed: %s", error)
                return None

            snapshot_file = self._write_screener_snapshot(payload)
            refreshed_at = datetime.fromtimestamp(snapshot_file.stat().st_mtime)
            self._set_market_data_status(
                healthy=True,
                available=True,
                message="Using fresh screener snapshot",
                source="live_refresh",
                file_name=snapshot_file.name,
                updated_at=refreshed_at.isoformat(),
                age_seconds=0.0,
            )
            return payload, snapshot_file, refreshed_at
        except Exception as e:
            self._set_market_data_status(
                healthy=False,
                available=False,
                message=f"Screener refresh error: {e}",
                source="live_refresh",
            )
            logger.error("AutoTrader screener refresh error: %s", e)
            return None

    # ------------------------------------------------------------------
    # High-level screener data fetch
    # ------------------------------------------------------------------

    def _fetch_screener_data(self) -> Optional[Dict[str, Dict]]:
        """
        Fetch latest market data from FYERS screener output.
        Returns dict of index -> market_data for NIFTY50, BANKNIFTY, SENSEX
        """
        try:
            latest_file = self._get_latest_screener_file()
            now = datetime.now()
            payload: Optional[Dict[str, Any]] = None

            if latest_file is not None:
                updated_at = datetime.fromtimestamp(latest_file.stat().st_mtime)
                age_seconds = (now - updated_at).total_seconds()
                if age_seconds <= self.screener_max_age_seconds:
                    payload = self._read_screener_payload(latest_file)
                    self._set_market_data_status(
                        healthy=True,
                        available=True,
                        message="Using fresh screener snapshot",
                        source="cache",
                        file_name=latest_file.name,
                        updated_at=updated_at.isoformat(),
                        age_seconds=age_seconds,
                    )
                else:
                    logger.warning(
                        "Latest screener snapshot is stale: %s (%ss old)",
                        latest_file.name,
                        int(age_seconds),
                    )

            if payload is None:
                cooldown_elapsed = (
                    self._last_screener_refresh_attempt is None
                    or (now - self._last_screener_refresh_attempt).total_seconds()
                    >= self.screener_refresh_cooldown_seconds
                )
                if cooldown_elapsed:
                    refreshed = self._refresh_screener_snapshot()
                    if refreshed is not None:
                        payload, _, _ = refreshed

            if payload is None:
                if latest_file is None:
                    self._set_market_data_status(
                        healthy=False,
                        available=False,
                        message="No screener snapshot available",
                        source="cache",
                    )
                else:
                    updated_at = datetime.fromtimestamp(latest_file.stat().st_mtime)
                    age_seconds = (now - updated_at).total_seconds()
                    self._set_market_data_status(
                        healthy=False,
                        available=True,
                        message=f"Screener data stale ({int(age_seconds)}s old)",
                        source="cache",
                        file_name=latest_file.name,
                        updated_at=updated_at.isoformat(),
                        age_seconds=age_seconds,
                    )
                return None

            market_data = self._build_market_data_from_payload(payload)
            return market_data if market_data else None

        except Exception as e:
            self._set_market_data_status(
                healthy=False,
                available=False,
                message=f"Error fetching screener data: {e}",
                source="auto_trader",
            )
            logger.error("Error fetching screener data: %s", e)
            return None

    # ------------------------------------------------------------------
    # Payload -> bot-consumable market data
    # ------------------------------------------------------------------

    def _build_market_data_from_payload(
        self, data: Dict[str, Any]
    ) -> Optional[Dict[str, Dict]]:
        """Transform screener JSON payload into bot-consumable index market data."""
        market_bias = data.get("market_bias", "NEUTRAL")

        # Use index_recommendations for direct index data (NIFTY50, BANKNIFTY, SENSEX)
        index_recs = data.get("index_recommendations", [])
        market_data: Dict[str, Dict] = {}

        for rec in index_recs:
            index = rec.get("index", "")
            if not index:
                continue

            market_data[index] = {
                "ltp": rec.get("ltp", 0) or 0,
                "change_pct": rec.get("change_pct", 0) or 0,
                "signal": rec.get("signal", "NEUTRAL"),
                "option_side": rec.get("option_side", ""),
                "atm_strike": rec.get("atm_strike", 0),
                "preferred_strike": rec.get("preferred_strike", 0),
                "strike_step": rec.get("strike_step", 50),
                "confidence": rec.get("confidence", 0) or 0,
                "reason": rec.get("reason", ""),
                "market_bias": market_bias,
                "candidate_strikes": rec.get("candidate_strikes", []),
            }

        # watchlist_baskets is a dict of lists {index: [stock_symbols]}
        # index_symbols is a dict of strings {index: "NSE:INDEX-INDEX"} -- not a stock list
        index_symbols = data.get("watchlist_baskets") or data.get("index_symbols", {})
        for index, symbols in index_symbols.items():
            if not isinstance(symbols, list):
                continue
            if index in market_data:
                market_data[index]["stocks"] = []
                for stock in data.get("results", []):
                    if stock.get("symbol") in symbols:
                        market_data[index]["stocks"].append({
                            "symbol": stock.get("symbol"),
                            "ltp": stock.get("last_price") or 0,
                            "change_pct": stock.get("change_pct") or 0,
                            "signal": stock.get("signal", ""),
                            "probability": stock.get("probability") or 50,
                        })

        if market_data:
            indices = list(market_data.keys())
            print(f"[AutoTrader] Loaded index data: {indices}, bias={market_bias}")
            for idx in indices:
                conf = market_data[idx].get("confidence", 0)
                side = market_data[idx].get("option_side", "?")
                chg = market_data[idx].get("change_pct", 0)
                print(
                    f"[AutoTrader]   {idx}: {side} signal, conf={conf}%, change={chg:.2f}%"
                )

        market_data = self._enhance_market_data(market_data)
        return market_data if market_data else None

    # ------------------------------------------------------------------
    # Market data enhancement (derived values for bot analysis)
    # ------------------------------------------------------------------

    def _enhance_market_data(self, market_data: Dict) -> Dict:
        """
        Enhance market data with derived values for bot analysis.
        Adds: prev_change_pct, momentum, estimated OI/PCR, IV estimates
        """
        for index, data in market_data.items():
            ltp = data.get("ltp", 0)
            change_pct = data.get("change_pct", 0)

            # Update price history
            if index not in self.price_history:
                self.price_history[index] = []

            self.price_history[index].append({
                "ltp": ltp,
                "change_pct": change_pct,
                "timestamp": datetime.now().isoformat(),
            })

            # Keep last 100 entries
            if len(self.price_history[index]) > 100:
                self.price_history[index] = self.price_history[index][-100:]

            # Calculate prev_change_pct from history
            history = self.price_history[index]
            if len(history) >= 2:
                data["prev_change_pct"] = history[-2]["change_pct"]
            else:
                data["prev_change_pct"] = change_pct * 0.95  # Small estimate

            # Calculate momentum
            data["momentum"] = change_pct - data["prev_change_pct"]

            # Estimate high/low from change if not available
            base_range_pct = 0.8  # Minimum intraday range
            extra_range = abs(change_pct) * 0.5
            total_range_pct = base_range_pct + extra_range

            if "high" not in data or data.get("high") is None:
                if change_pct >= 0:
                    data["high"] = ltp * (1 + total_range_pct / 100 * 0.6)
                    data["low"] = ltp * (1 - total_range_pct / 100 * 0.4)
                else:
                    data["high"] = ltp * (1 + total_range_pct / 100 * 0.4)
                    data["low"] = ltp * (1 - total_range_pct / 100 * 0.6)
            if "low" not in data or data.get("low") is None:
                data["low"] = ltp * (1 - total_range_pct / 100 * 0.5)

            # Inject open price if missing
            if "open" not in data or data.get("open") is None:
                if ltp > 0 and change_pct != 0:
                    data["open"] = ltp / (1 + change_pct / 100)
                else:
                    data["open"] = ltp

            # --- OI data: prefer live FYERS option chain, fall back to heuristic ---
            oi_live = self._fetch_live_oi(index, ltp)
            if oi_live:
                data["ce_oi"] = oi_live["ce_oi"]
                data["pe_oi"] = oi_live["pe_oi"]
                data["pcr"] = oi_live["pcr"]
                data["ce_oi_change"] = oi_live["ce_oi_change"]
                data["pe_oi_change"] = oi_live["pe_oi_change"]
                data["oi_source"] = "live"
                print(
                    f"[OI-LIVE] {index}  ce_oi={oi_live['ce_oi']}  pe_oi={oi_live['pe_oi']}"
                    f"  pcr={oi_live['pcr']:.3f}  ce_chg={oi_live['ce_oi_change']}"
                    f"  pe_chg={oi_live['pe_oi_change']}"
                )
            else:
                # Heuristic fallback (synthetic OI)
                signal = data.get("signal", "NEUTRAL")
                if signal == "BULLISH" or data.get("option_side") == "CE":
                    data["pcr"] = 1.1 + (data.get("confidence", 50) / 100 * 0.4)
                elif signal == "BEARISH" or data.get("option_side") == "PE":
                    data["pcr"] = 0.9 - (data.get("confidence", 50) / 100 * 0.4)
                else:
                    data["pcr"] = 1.0

                conf = data.get("confidence", 50)
                if change_pct > 0:
                    data["ce_oi"] = 100000 * (1 + conf / 100)
                    data["pe_oi"] = 100000 * (1 - conf / 200)
                    data["ce_oi_change"] = 5 if conf > 60 else -5
                    data["pe_oi_change"] = -5 if conf > 60 else 5
                else:
                    data["ce_oi"] = 100000 * (1 - conf / 200)
                    data["pe_oi"] = 100000 * (1 + conf / 100)
                    data["ce_oi_change"] = -5 if conf > 60 else 5
                    data["pe_oi_change"] = 5 if conf > 60 else -5
                data["oi_source"] = "heuristic"
                print(f"[OI-HEURISTIC] {index} — live OI unavailable, using synthetic data")

            # Estimate IV percentile from volatility (heuristic)
            range_pct = abs(change_pct)
            if range_pct > 1.5:
                data["iv_percentile"] = 80 + min(20, range_pct * 5)
            elif range_pct > 0.8:
                data["iv_percentile"] = 50 + range_pct * 20
            else:
                data["iv_percentile"] = 30 + range_pct * 25

            # Estimate VIX from market movement
            data["vix"] = 12 + abs(change_pct) * 5

            # Set volume estimates
            data["volume"] = 1000000
            data["avg_volume"] = 1000000

        return market_data

    # ------------------------------------------------------------------
    # Live OI fetching from FYERS option chain
    # ------------------------------------------------------------------

    def _fetch_live_oi(self, index: str, ltp: float) -> Optional[Dict[str, Any]]:
        """Fetch real OI data from FYERS option chain API.

        Returns dict with ce_oi, pe_oi, pcr, ce_oi_change, pe_oi_change
        or None on failure.
        """
        fyers_symbol = self._OI_INDEX_SYMBOLS.get(index)
        if not fyers_symbol:
            print(f"[OI-LIVE] No FYERS symbol mapping for index {index}")
            return None

        try:
            # Use FyersClient directly (not the Kafka market data client)
            if not hasattr(self, "_fyers_oi_client"):
                try:
                    from shared_project_engine.auth.fyers_client import FyersClient
                    self._fyers_oi_client = FyersClient()
                    print(f"[OI-LIVE] FyersClient created, token={'SET' if self._fyers_oi_client.access_token else 'MISSING'}")
                except Exception as e:
                    print(f"[OI-LIVE] Cannot create FyersClient: {e}")
                    self._fyers_oi_client = None

            client = self._fyers_oi_client
            if not client:
                print("[OI-LIVE] No FyersClient available")
                return None

            result = client.option_chain(fyers_symbol, strike_count=10)
            if not result.get("success"):
                print(f"[OI-LIVE] option_chain failed for {index}: {result.get('error', 'unknown')}")
                return None

            chain_data = result.get("data", {})
            # FYERS wraps in a nested 'data' key
            if isinstance(chain_data, dict) and "data" in chain_data:
                chain_data = chain_data["data"]

            # Total OI from summary fields
            total_ce_oi = chain_data.get("callOi", 0)
            total_pe_oi = chain_data.get("putOi", 0)

            # Per-contract OI for ATM region (for change signals)
            contracts = chain_data.get("optionsChain", [])
            ce_oi_near = 0
            pe_oi_near = 0
            for c in contracts:
                strike = c.get("strike_price", 0)
                if strike <= 0:
                    continue  # skip underlying row
                ot = c.get("option_type", "")
                oi = c.get("oi", 0) or 0
                # Focus on ATM +/- 2 strikes
                if ltp > 0 and abs(strike - ltp) / ltp <= 0.02:
                    if ot == "CE":
                        ce_oi_near += oi
                    elif ot == "PE":
                        pe_oi_near += oi

            pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

            # OI change: compare against cached previous snapshot
            cache_key = f"_oi_prev_{index}"
            prev = getattr(self, cache_key, None)
            ce_oi_change = 0
            pe_oi_change = 0
            if prev:
                ce_oi_change = total_ce_oi - prev["ce_oi"]
                pe_oi_change = total_pe_oi - prev["pe_oi"]
            setattr(self, cache_key, {"ce_oi": total_ce_oi, "pe_oi": total_pe_oi})

            return {
                "ce_oi": total_ce_oi,
                "pe_oi": total_pe_oi,
                "pcr": round(pcr, 4),
                "ce_oi_change": ce_oi_change,
                "pe_oi_change": pe_oi_change,
            }

        except Exception as e:
            print(f"[OI-LIVE] Failed to fetch OI for {index}: {e}")
            return None

    # ------------------------------------------------------------------
    # Live price fetching for position monitoring
    # ------------------------------------------------------------------

    def _get_current_prices(self, market_data: Dict) -> Dict[str, float]:
        """Get current prices for open positions from market data."""
        from .auto_trader import TradingMode, _build_fyers_option_symbol

        prices: Dict[str, float] = {}

        # Lazily initialise market data client for live option quote fetching
        self._get_market_data_client()

        for pos in self.positions.values():
            if pos.status != "open":
                continue

            # Try to find price in market data
            index_data = market_data.get(pos.index, {})

            # Step 1: try to get option price from stocks list
            for stock in index_data.get("stocks", []):
                if stock.get("symbol") == pos.symbol:
                    prices[pos.symbol] = stock.get("ltp", pos.entry_price)
                    break

            # Step 2: fetch real option LTP via Fyers API
            if (
                pos.symbol not in prices
                and self._market_data_client
                and pos.strike
            ):
                fyers_sym = _build_fyers_option_symbol(
                    pos.index, pos.strike, pos.option_type
                )
                if fyers_sym:
                    try:
                        ltp = self._market_data_client.get_quote_ltp(
                            fyers_sym, ttl_seconds=5
                        )
                        if ltp > 0:
                            prices[pos.symbol] = ltp
                            logger.debug(
                                "Option LTP from Fyers: %s = %s", fyers_sym, ltp
                            )
                    except Exception as e:
                        logger.warning(
                            "Fyers quote fetch failed for %s: %s "
                            "-- SL/target check skipped this cycle",
                            fyers_sym, e,
                        )

            # Step 3: final fallback for paper trading -- simulate via index change
            if (
                pos.symbol not in prices
                and self.mode == TradingMode.PAPER
                and index_data
            ):
                index_change_pct = index_data.get("change_pct", 0)
                if pos.option_type == "CE":
                    option_change = index_change_pct * 2.5
                else:  # PE
                    option_change = -index_change_pct * 2.5

                prices[pos.symbol] = pos.entry_price * (1 + option_change / 100)

        return prices

    # ------------------------------------------------------------------
    # Manual market data feed
    # ------------------------------------------------------------------

    def feed_market_data(self, index: str, market_data: Dict) -> Dict:
        """
        Manual market data feed -- called by external systems.
        Use this when you want to feed data directly instead of polling screener.

        Returns the signal/action result.
        """
        if not self.is_running or self.is_paused:
            return {"action": "SKIP", "reason": "Auto-trader not running"}

        signal = self.process_signal(index, market_data)

        if signal and signal.get("action") == "TRADE":
            position = self.execute_trade(signal)
            if position:
                return {
                    "action": "EXECUTED",
                    "position_id": position.id,
                    "symbol": position.symbol,
                    "entry_price": position.entry_price,
                    "stop_loss": position.stop_loss,
                    "target": position.target,
                }

        return signal or {"action": "SKIP", "reason": "No signal"}
