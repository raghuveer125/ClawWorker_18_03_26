"""
Canonical DB layer — non-blocking writer, schemas, repositories.
"""

from data_platform.db.pool import (
    DBPoolConfig,
    close_pool,
    get_pool,
)
from data_platform.db.writer import (
    DBRoute,
    DBSink,
    DBTopicRouter,
    DBWriteRecord,
    DrainSummary,
    EnqueueResult,
    InMemoryDBSink,
    NonBlockingDBWriter,
)
from data_platform.db.ohlcv_history import (
    OHLCVHistoryConfig,
    OHLCVRecord,
    PostgresOHLCVRepository,
    sync_ensure_ohlcv_schema,
    sync_upsert_ohlcv,
    sync_prune_ohlcv,
    sync_ohlcv_summary,
    sync_ohlcv_latest_timestamps,
    sync_ohlcv_for_symbol,
    sync_ohlcv_for_index,
)
from data_platform.db.watchlist import (
    PostgresWatchlistRepository,
    WatchlistConfig,
    sync_add_symbols,
    sync_deactivate_symbol,
    sync_ensure_schema,
    sync_list_active_symbols,
    watchlist_from_sources,
)
from data_platform.db.trades import (
    TradeRecord,
    TradesConfig,
    PostgresTradesRepository,
    sync_ensure_trades_schema,
    sync_insert_trade,
    sync_update_trade_exit,
    sync_trades_for_strategy,
    sync_trades_summary,
)
from data_platform.db.strategy_config import (
    StrategyConfig,
    StrategyConfigDBConfig,
    PostgresStrategyConfigRepository,
    sync_ensure_strategy_config_schema,
    sync_seed_strategy_defaults,
    sync_load_strategy_config,
    sync_load_all_strategy_configs,
    sync_upsert_strategy_config,
    sync_set_strategy_enabled,
)

__all__ = [
    # Pool
    "DBPoolConfig", "get_pool", "close_pool",
    # Writer
    "DBSink", "InMemoryDBSink", "DBRoute", "DBTopicRouter",
    "DBWriteRecord", "EnqueueResult", "DrainSummary", "NonBlockingDBWriter",
    # OHLCV
    "OHLCVHistoryConfig", "OHLCVRecord", "PostgresOHLCVRepository",
    "sync_ensure_ohlcv_schema", "sync_upsert_ohlcv", "sync_prune_ohlcv",
    "sync_ohlcv_summary", "sync_ohlcv_latest_timestamps",
    "sync_ohlcv_for_symbol", "sync_ohlcv_for_index",
    # Watchlist
    "WatchlistConfig", "PostgresWatchlistRepository", "watchlist_from_sources",
    "sync_ensure_schema", "sync_list_active_symbols",
    "sync_add_symbols", "sync_deactivate_symbol",
    # Trades (ClawWorker-specific)
    "TradeRecord", "TradesConfig", "PostgresTradesRepository",
    "sync_ensure_trades_schema", "sync_insert_trade", "sync_update_trade_exit",
    "sync_trades_for_strategy", "sync_trades_summary",
    # Strategy config (ClawWorker-specific)
    "StrategyConfig", "StrategyConfigDBConfig", "PostgresStrategyConfigRepository",
    "sync_ensure_strategy_config_schema", "sync_seed_strategy_defaults",
    "sync_load_strategy_config", "sync_load_all_strategy_configs",
    "sync_upsert_strategy_config", "sync_set_strategy_enabled",
]
