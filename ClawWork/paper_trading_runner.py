#!/usr/bin/env python3
"""
Paper Trading Runner - Phase 1B Integration
Connects ICT Sniper Phase 1B to FastAPI endpoint and records trades in real-time

This runner:
1. Fetches live market data for NIFTY50, BANKNIFTY, FINNIFTY
2. Generates signals via the ensemble API
3. Records trade outcomes as they occur
4. Displays real-time metrics via the dashboard
"""

import os
import sys
import json
import time
import asyncio
import logging
from pathlib import Path
from datetime import datetime, time as dt_time
from typing import Dict, List, Optional
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("PaperTrading")

load_dotenv()

# Import shared engine modules
_PROJECT_ROOT = Path(__file__).resolve().parent
_SHARED_ROOT = _PROJECT_ROOT.parent
if str(_SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(_SHARED_ROOT))

try:
    from shared_project_engine.indices import (
        ACTIVE_INDICES,
        canonicalize_index_name,
        get_market_index_config,
    )
    from data_platform.market_consumer import KafkaMarketDataClient as MarketDataClient
    from shared_project_engine.strategy_isolation import (
        DEFAULT_LEGACY_RUNNER_STRATEGY_ID,
        StrategyRuntimeLock,
        normalize_strategy_id,
        resolve_strategy_component_dir,
    )

    _SHARED_INDICES = ACTIVE_INDICES
except ImportError:
    _SHARED_INDICES = ["SENSEX", "NIFTY50", "BANKNIFTY", "FINNIFTY"]

    def canonicalize_index_name(index_name: str) -> str:
        return str(index_name or "SENSEX").upper()

    def get_market_index_config(index_name: str) -> Dict[str, str]:
        mapping = {
            "SENSEX": "BSE:SENSEX-INDEX",
            "NIFTY50": "NSE:NIFTY50-INDEX",
            "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
            "FINNIFTY": "NSE:FINNIFTY-INDEX",
        }
        key = canonicalize_index_name(index_name)
        return {"spot_symbol": mapping.get(key, "")}

    MarketDataClient = None
    DEFAULT_LEGACY_RUNNER_STRATEGY_ID = "legacy-ict-runner"

    class StrategyRuntimeLock:
        def __init__(self, runtime_dir, strategy_id, component):
            self.runtime_dir = Path(runtime_dir)

        def acquire(self, extra_metadata=None):
            self.runtime_dir.mkdir(parents=True, exist_ok=True)

        def release(self):
            return None

    def normalize_strategy_id(value: Optional[str], default: str) -> str:
        return str(value or default).strip() or default

    def resolve_strategy_component_dir(root_dir: Path, strategy_id: str, component: str) -> Path:
        return Path(root_dir) / strategy_id / component

# Configuration
API_URL = os.getenv("API_URL", "http://localhost:8001/api")
MARKET_DATA_PROVIDER = os.getenv("MARKET_DATA_PROVIDER", "fyers")  # fyers or mock
INDICES = _SHARED_INDICES
TRADING_START_HOUR = 9  # 9:15 AM
TRADING_END_HOUR = 15   # 3:30 PM

# Phase 1B Configuration
PHASE_1B_CONFIG = {
    "swing_lookback": 9,
    "mss_swing_len": 2,
    "vol_multiplier": 1.2,
    "displacement_multiplier": 1.3,
    "mtf_mode": "permissive",
    "confidence_gate": 70.0,
}


def resolve_runner_strategy_id(explicit: Optional[str] = None) -> str:
    return normalize_strategy_id(
        explicit or os.getenv("PAPER_TRADING_STRATEGY_ID"),
        DEFAULT_LEGACY_RUNNER_STRATEGY_ID,
    )


def _default_runner_data_root() -> Path:
    return Path(__file__).resolve().parent / "data" / "paper_strategies"


def _default_runner_runtime_root() -> Path:
    return Path(__file__).resolve().parent / "data" / "paper_strategies_runtime"


def resolve_runner_data_dir(strategy_id: Optional[str] = None) -> Path:
    explicit_dir = os.getenv("PAPER_TRADING_DATA_DIR")
    if explicit_dir:
        return Path(explicit_dir)
    normalized_strategy = resolve_runner_strategy_id(strategy_id)
    return resolve_strategy_component_dir(
        Path(os.getenv("PAPER_TRADING_DATA_ROOT", str(_default_runner_data_root()))),
        normalized_strategy,
        "legacy_runner",
    )


def resolve_runner_runtime_dir(strategy_id: Optional[str] = None) -> Path:
    normalized_strategy = resolve_runner_strategy_id(strategy_id)
    return Path(os.getenv("PAPER_TRADING_RUNTIME_ROOT", str(_default_runner_runtime_root()))) / normalized_strategy

@dataclass
class TradeSignal:
    """Represents a potential trade signal"""
    index: str
    signal_type: str  # BUY, SELL
    confidence: float
    entry_price: float
    stop_loss: float
    target: float
    timestamp: str
    analysis: Dict
    action: str = ""
    option_type: str = ""
    strike: int = 0
    strategy_id: str = ""

@dataclass
class OpenTrade:
    """Represents an open trade"""
    index: str
    signal_type: str
    entry_price: float
    stop_loss: float
    target: float
    entry_time: str
    entry_analysis: Dict
    action: str = ""
    option_type: str = ""
    strike: int = 0
    strategy_id: str = ""

class MarketDataProvider:
    """Base class for market data providers"""
    
    async def fetch_market_data(self, index: str) -> Optional[Dict]:
        """Fetch current market data for index"""
        raise NotImplementedError
    
    async def is_market_open(self) -> bool:
        """Check if market is currently open"""
        now = datetime.now().time()
        start = dt_time(TRADING_START_HOUR, 15)
        end = dt_time(TRADING_END_HOUR, 30)
        is_weekday = datetime.now().weekday() < 5
        return is_weekday and start <= now <= end

class FyersDataProvider(MarketDataProvider):
    """Fyers market data provider"""

    def __init__(self):
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN", "")
        self.client = (
            MarketDataClient(fallback_to_local=bool(self.access_token))
            if MarketDataClient is not None
            else None
        )
        self._last_candle_epoch: Dict[str, int] = {}

    @staticmethod
    def _parse_history_candles(payload: Dict) -> List[List[float]]:
        data = payload.get("data", {})
        if isinstance(data, dict) and isinstance(data.get("candles"), list):
            return data["candles"]
        if isinstance(payload.get("candles"), list):
            return payload["candles"]
        return []

    @staticmethod
    def _latest_completed_candle(candles: List[List[float]]) -> Optional[List[float]]:
        if not candles:
            return None

        now_epoch = int(time.time())
        current_minute_epoch = now_epoch - (now_epoch % 60)
        completed: List[List[float]] = []
        for candle in candles:
            if not isinstance(candle, list) or len(candle) < 6:
                continue
            epoch_value = float(candle[0])
            if epoch_value > 1_000_000_000_000:
                epoch_value /= 1000.0
            if int(epoch_value) < current_minute_epoch:
                completed.append(candle)

        return completed[-1] if completed else candles[-1]

    async def fetch_market_data(self, index: str) -> Optional[Dict]:
        """Fetch from Fyers API"""
        try:
            if self.client is None:
                if not self.access_token:
                    logger.warning("FYERS_ACCESS_TOKEN not set and market adapter unavailable, using mock data")
                return self._mock_market_data(index)
            return await asyncio.to_thread(self._fetch_market_data_sync, index)
        except Exception as e:
            logger.error(f"Fyers API error: {e}, using mock data")
        
        return self._mock_market_data(index)

    def _fetch_market_data_sync(self, index: str) -> Dict:
        canonical_index = canonicalize_index_name(index)
        config = get_market_index_config(canonical_index)
        symbol = str(config.get("spot_symbol", "")).strip()
        if not symbol:
            return self._mock_market_data(canonical_index)

        history = self.client.get_history_snapshot(
            symbol=symbol,
            resolution="1",
            lookback_days=1,
        )
        candles = self._parse_history_candles(history)
        latest_candle = self._latest_completed_candle(candles)
        if latest_candle is None or len(latest_candle) < 6:
            return self._mock_market_data(canonical_index)

        candle_epoch = float(latest_candle[0])
        if candle_epoch > 1_000_000_000_000:
            candle_epoch /= 1000.0
        candle_epoch_int = int(candle_epoch)
        if self._last_candle_epoch.get(canonical_index) == candle_epoch_int:
            return {}
        self._last_candle_epoch[canonical_index] = candle_epoch_int

        prev_close = float(latest_candle[4] or 0.0)
        if len(candles) >= 2 and isinstance(candles[-2], list) and len(candles[-2]) >= 5:
            prev_close = float(candles[-2][4] or prev_close)

        close = float(latest_candle[4] or 0.0)
        if close <= 0:
            return {}
        change_pct = ((close - prev_close) / prev_close * 100.0) if prev_close else 0.0

        return {
            "index": canonical_index,
            "ltp": close,
            "open": float(latest_candle[1] or close),
            "high": float(latest_candle[2] or close),
            "low": float(latest_candle[3] or close),
            "close": close,
            "change_pct": round(change_pct, 2),
            "volume": int(float(latest_candle[5] or 0.0)),
            "bar_index": candle_epoch_int // 60,
            "timestamp": datetime.fromtimestamp(candle_epoch_int).isoformat(),
        }
    
    def _mock_market_data(self, index: str) -> Dict:
        """Generate mock market data for testing"""
        import random
        base_prices = {"SENSEX": 74000, "NIFTY50": 24500, "BANKNIFTY": 49000, "FINNIFTY": 23500}
        base = base_prices.get(index, 10000)
        variation = random.uniform(-0.5, 0.5)
        
        return {
            "index": index,
            "ltp": base * (1 + variation/100),
            "high": base * (1 + abs(variation)/100),
            "low": base * (1 - abs(variation)/100),
            "open": base,
            "close": base * (1 + variation/100),
            "volume": random.randint(1000000, 5000000),
            "timestamp": datetime.now().isoformat(),
        }

class PaperTradingEngine:
    """Main paper trading engine"""
    
    def __init__(self, api_url: str, data_provider: MarketDataProvider, strategy_id: Optional[str] = None):
        self.api_url = api_url
        self.data_provider = data_provider
        self.strategy_id = resolve_runner_strategy_id(strategy_id)
        self.data_dir = resolve_runner_data_dir(self.strategy_id)
        self.state_file = self.data_dir / "paper_trading_state.json"
        self.trade_log_file = self.data_dir / "paper_trading.jsonl"
        self.runtime_dir = resolve_runner_runtime_dir(self.strategy_id)
        self.runtime_lock = StrategyRuntimeLock(
            runtime_dir=self.runtime_dir,
            strategy_id=self.strategy_id,
            component="legacy_runner",
        )
        self.open_trades: Dict[str, OpenTrade] = {}
        self.daily_stats = self._init_daily_stats()
        self._load_state()
    
    def _init_daily_stats(self) -> Dict:
        """Initialize daily statistics"""
        return {
            "timestamp": datetime.now().isoformat(),
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "daily_pnl": 0.0,
            "signals_analyzed": 0,
            "trades_taken": 0,
        }
    
    def _load_state(self):
        """Load previous session state"""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    self.open_trades = {
                        k: OpenTrade(**v) for k, v in data.get("open_trades", {}).items()
                    }
                    self.daily_stats = data.get("daily_stats", self._init_daily_stats())
                    logger.info(f"Loaded state: {len(self.open_trades)} open trades")
            except Exception as e:
                logger.error(f"Error loading state: {e}")
    
    def _save_state(self):
        """Save current session state"""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "open_trades": {k: v.__dict__ for k, v in self.open_trades.items()},
                    "daily_stats": self.daily_stats,
                    "strategy_id": self.strategy_id,
                }, f)
        except Exception as e:
            logger.error(f"Error saving state: {e}")
    
    async def analyze_and_execute(self, index: str, market_data: Dict) -> Optional[TradeSignal]:
        """Get signal from API and execute if valid"""
        try:
            # Call direct ICT Sniper API with completed 1m candle data
            response = requests.post(
                f"{self.api_url}/bots/ict-sniper/analyze",
                json={"index": index, "market_data": market_data},
                timeout=5
            )
            response.raise_for_status()
            result = response.json()
            
            # Check if we have a valid signal
            decision = result.get("decision", {})
            if not decision or decision.get("action") == "NO_TRADE":
                return None
            
            confidence = decision.get("confidence", 0)
            # Extract signal_type from action (BUY_CE -> BUY, BUY_PE -> SELL)
            action = decision.get("action", "")
            if "CE" in action:
                signal_type = "BUY"
            elif "PE" in action:
                signal_type = "SELL"
            else:
                signal_type = ""

            # Apply confidence gate
            if confidence < PHASE_1B_CONFIG["confidence_gate"]:
                logger.debug(f"{index}: Signal {signal_type} rejected (confidence: {confidence:.1f}%)")
                return None

            if not signal_type:
                logger.debug(f"{index}: No valid signal_type from action '{action}'")
                return None

            # Create trade signal
            entry_price = float(decision.get("entry") or market_data.get("close") or market_data.get("ltp") or 0.0)
            stop_loss = float(decision.get("stop_loss") or 0.0)
            target = float(decision.get("target") or 0.0)
            option_type = "CE" if "CE" in action else "PE" if "PE" in action else ""
            
            signal = TradeSignal(
                index=index,
                signal_type=signal_type,
                action=action,
                option_type=option_type,
                confidence=confidence,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target=target,
                timestamp=str(decision.get("timestamp") or market_data.get("timestamp") or datetime.now().isoformat()),
                analysis=decision.get("analysis", {}),
                strike=int(float(decision.get("strike", 0) or 0)),
                strategy_id=self.strategy_id,
            )
            
            logger.info(f"✓ {index}: {signal_type} signal (confidence: {confidence:.1f}%) @ {entry_price:.2f}")
            
            # Record trade entry
            self.open_trades[f"{index}_{datetime.now().timestamp()}"] = OpenTrade(
                index=index,
                signal_type=signal_type,
                action=action,
                option_type=option_type,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target=target,
                entry_time=signal.timestamp,
                entry_analysis=decision,
                strike=signal.strike,
                strategy_id=self.strategy_id,
            )
            self.daily_stats["trades_taken"] += 1
            self._save_state()
            
            return signal
        
        except Exception as e:
            logger.error(f"Error analyzing {index}: {e}")
            return None
    
    async def update_open_trades(self, market_data: Dict):
        """Update open trades based on new market data"""
        index = market_data.get("index")
        ltp = market_data.get("ltp", 0)
        
        trades_to_close = []
        
        for trade_id, trade in list(self.open_trades.items()):
            if trade.index != index:
                continue
            
            is_win = False
            is_loss = False
            pnl = 0
            
            if trade.signal_type == "BUY":
                if ltp >= trade.target:
                    is_win = True
                    pnl = (trade.target - trade.entry_price) * 1  # 1 lot
                elif ltp <= trade.stop_loss:
                    is_loss = True
                    pnl = (trade.stop_loss - trade.entry_price) * 1
            else:  # SELL
                if ltp <= trade.target:
                    is_win = True
                    pnl = (trade.entry_price - trade.target) * 1
                elif ltp >= trade.stop_loss:
                    is_loss = True
                    pnl = (trade.entry_price - trade.stop_loss) * 1
            
            if is_win or is_loss:
                outcome = "WIN" if is_win else "LOSS"
                trades_to_close.append((trade_id, trade, outcome, pnl))
        
        # Close trades and record outcomes
        for trade_id, trade, outcome, pnl in trades_to_close:
            self._record_trade_outcome(trade, outcome, pnl)
            del self.open_trades[trade_id]
        
        self._save_state()
    
    def _record_trade_outcome(self, trade: OpenTrade, outcome: str, pnl: float):
        """Record trade outcome to API and log"""
        try:
            # Record to API
            response = requests.post(
                f"{self.api_url}/bots/ict-sniper/record-trade",
                json={
                    "index": trade.index,
                    "trade_id": f"{trade.index}_{trade.entry_time}",
                    "action": trade.action,
                    "option_type": trade.option_type,
                    "strike": trade.strike,
                    "entry_price": trade.entry_price,
                    "entry_time": trade.entry_time,
                    "exit_price": trade.target if outcome == "WIN" else trade.stop_loss,
                    "outcome": outcome,
                    "pnl": pnl,
                    "reasoning": str(trade.entry_analysis.get("reasoning", "")),
                    "market_data": dict(trade.entry_analysis.get("analysis", {}) or {}),
                },
                timeout=5
            )
            response.raise_for_status()
            
            # Update stats
            self.daily_stats["total_trades"] += 1
            if outcome == "WIN":
                self.daily_stats["wins"] += 1
            elif outcome == "LOSS":
                self.daily_stats["losses"] += 1
            else:
                self.daily_stats["breakeven"] += 1
            self.daily_stats["daily_pnl"] += pnl
            
            # Log to file
            self.trade_log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.trade_log_file, "a") as f:
                f.write(json.dumps({
                    "timestamp": datetime.now().isoformat(),
                    "strategy_id": self.strategy_id,
                    "index": trade.index,
                    "signal_type": trade.signal_type,
                    "action": trade.action,
                    "option_type": trade.option_type,
                    "strike": trade.strike,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.target if outcome == "WIN" else trade.stop_loss,
                    "outcome": outcome,
                    "pnl": pnl,
                    "analysis": trade.entry_analysis,
                }) + "\n")
            
            status = "✓ WIN" if outcome == "WIN" else "✗ LOSS"
            logger.info(f"{status}: {trade.index} {trade.signal_type} @ {trade.entry_price:.2f} → ₹{pnl:.0f}")
        
        except Exception as e:
            logger.error(f"Error recording trade outcome: {e}")
    
    async def run_trading_loop(self, interval: int = 60):
        """Main trading loop - analyzes market and updates trades"""
        self.runtime_lock.acquire(
            extra_metadata={
                "api_url": self.api_url,
                "data_dir": str(self.data_dir),
            }
        )
        logger.info("🚀 Paper Trading Loop Started")
        logger.info("🧭 Strategy ID: %s", self.strategy_id)
        logger.info(f"📊 Phase 1B Config: {PHASE_1B_CONFIG}")
        logger.info(f"📈 Monitoring: {INDICES}")
        
        try:
            while True:
                try:
                    # Check market hours
                    if not await self.data_provider.is_market_open():
                        logger.debug("Market closed, sleeping...")
                        await asyncio.sleep(60)
                        continue

                    # Fetch data for all indices
                    market_data_map = {}
                    for index in INDICES:
                        data = await self.data_provider.fetch_market_data(index)
                        if data:
                            market_data_map[index] = data

                    # Analyze each index
                    for index, market_data in market_data_map.items():
                        await self.analyze_and_execute(index, market_data)
                        await self.update_open_trades(market_data)

                    # Display status every 10 loops
                    self.daily_stats["signals_analyzed"] += len(market_data_map)
                    if self.daily_stats["signals_analyzed"] % 10 == 0:
                        self._log_status()

                    await asyncio.sleep(interval)
                except KeyboardInterrupt:
                    logger.info("⏹️ Shutting down paper trading...")
                    self._save_state()
                    break
                except Exception as e:
                    logger.error(f"Error in trading loop: {e}")
                    await asyncio.sleep(interval)
            
        finally:
            self.runtime_lock.release()
    
    def _log_status(self):
        """Log current trading status"""
        wr = self.daily_stats["wins"] / max(self.daily_stats["total_trades"], 1)
        logger.info(
            f"📊 Status: {self.daily_stats['trades_taken']} signals analyzed | "
            f"{self.daily_stats['total_trades']} trades closed | "
            f"WR: {wr:.1%} | P&L: ₹{self.daily_stats['daily_pnl']:.0f} | "
            f"Open: {len(self.open_trades)}"
        )

async def main():
    """Main entry point"""
    logger.info("🎯 Paper Trading Runner - Phase 1B")
    logger.info(f"API: {API_URL}")
    logger.info(f"Data Provider: {MARKET_DATA_PROVIDER}")
    
    # Initialize data provider
    if MARKET_DATA_PROVIDER == "fyers":
        data_provider = FyersDataProvider()
    else:
        logger.warning("Unknown data provider, using mock data")
        data_provider = FyersDataProvider()
    
    # Initialize trading engine
    engine = PaperTradingEngine(API_URL, data_provider)
    logger.info("Strategy ID: %s", engine.strategy_id)
    logger.info("Data Dir: %s", engine.data_dir)
    
    # Run trading loop
    try:
        await engine.run_trading_loop(interval=60)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
