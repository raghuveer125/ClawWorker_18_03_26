from config import SignalConfig
from models import MarketInput, RiskChecks


def evaluate_risk_guards(market: MarketInput, config: SignalConfig) -> RiskChecks:
    daily_loss_guard = "PASS" if market.daily_realized_pnl_pct > -config.max_daily_loss_pct else "FAIL"
    spread_guard = "PASS" if market.bid_ask_spread_bps <= config.max_spread_bps else "FAIL"

    data_quality_ok = (
        bool(market.timestamp)
        and bool(market.underlying)
        and market.ltp is not None
        and market.prev_close is not None
        and market.prev_close > 0
    )
    data_quality_guard = "PASS" if data_quality_ok else "FAIL"

    return RiskChecks(
        daily_loss_guard=daily_loss_guard,
        spread_guard=spread_guard,
        data_quality_guard=data_quality_guard,
    )


def has_veto(risk_checks: RiskChecks) -> bool:
    return any(
        guard == "FAIL"
        for guard in [
            risk_checks.daily_loss_guard,
            risk_checks.spread_guard,
            risk_checks.data_quality_guard,
        ]
    )
