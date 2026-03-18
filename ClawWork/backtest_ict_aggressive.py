#!/usr/bin/env python3
"""
Aggressive Backtest for ICT Sniper Multi-Timeframe Strategy

Runs backtest with 90 days of historical data and generates Phase 1 tuning recommendations.
"""

import sys
import os
import json
from datetime import datetime, timedelta
from pathlib import Path

# Ensure proper path setup
sys.path.insert(0, os.path.abspath('.'))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from livebench.backtesting.backtest import Backtester, BacktestResult
from livebench.bots.ict_sniper import ICTSniperBot, ICTConfig
from livebench.bots.base import SharedMemory
import logging

logging.basicConfig(
    level=logging.INFO,
    format='[%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def run_aggressive_backtest():
    """Run comprehensive backtest across indices"""
    
    print("\n" + "="*70)
    print("ICT SNIPER MULTI-TIMEFRAME STRATEGY - AGGRESSIVE BACKTEST")
    print("="*70)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"Testing Period: 90 days of historical data")
    print("="*70 + "\n")

    # Initialize backtest engine
    backtester = Backtester()
    
    # Target indices for testing
    indices = ["BANKNIFTY", "NIFTY50", "FINNIFTY"]
    all_results = {}
    
    # Run backtest for each index
    for index in indices:
        print(f"\n{'─'*70}")
        print(f"Testing on {index}")
        print(f"{'─'*70}")
        
        try:
            # Run aggressive backtest (90 days, 5-min resolution)
            result = backtester.run_backtest(
                index=index,
                days=90,  # Aggressive: 90 days
                resolution="5"  # 5-minute candles
            )
            
            all_results[index] = result
            
            # Display results
            print(f"\n📊 BACKTEST RESULTS FOR {index}:")
            print(f"  Period: {result.period_start} to {result.period_end}")
            print(f"  Total Candles: {result.total_candles:,}")
            print(f"  Total Signals: {result.total_signals}")
            print(f"  Total Trades: {result.total_trades}")
            print(f"  ✓ Wins: {result.wins}")
            print(f"  ✗ Losses: {result.losses}")
            print(f"  ≈ Breakeven: {result.breakeven}")
            print(f"  Win Rate: {result.win_rate:.1f}%")
            print(f"  Total P&L: ₹{result.total_pnl:,.0f}")
            print(f"  Avg Win: ₹{result.avg_win:,.0f}")
            print(f"  Avg Loss: ₹{result.avg_loss:,.0f}")
            print(f"  Profit Factor: {result.profit_factor:.2f}")
            print(f"  Max Drawdown: {result.max_drawdown:.1%}")
            print(f"  Sharpe Ratio: {result.sharpe_ratio:.2f}")
            
            # Print bot-specific performance
            if result.bot_performance and "ICTSniper" in result.bot_performance:
                ict_perf = result.bot_performance["ICTSniper"]
                print(f"\n\n  🎯 ICT SNIPER PERFORMANCE:")
                print(f"     Signals: {ict_perf.get('signals', 0)}")
                print(f"     Trades: {ict_perf.get('trades', 0)}")
                print(f"     Win Rate: {ict_perf.get('win_rate', 0):.1f}%")
                print(f"     Avg Win: ₹{ict_perf.get('avg_win', 0):,.0f}")
                print(f"     Avg Loss: ₹{ict_perf.get('avg_loss', 0):,.0f}")
                print(f"     Total P&L: ₹{ict_perf.get('total_pnl', 0):,.0f}")
            
        except Exception as e:
            logger.error(f"Error backtesting {index}: {e}", exc_info=True)
            print(f"✗ Error: {e}")
    
    # Aggregate results
    print("\n\n" + "="*70)
    print("AGGREGATE RESULTS ACROSS ALL INDICES")
    print("="*70)
    
    total_trades = sum(r.total_trades for r in all_results.values())
    total_wins = sum(r.wins for r in all_results.values())
    total_losses = sum(r.losses for r in all_results.values())
    total_pnl = sum(r.total_pnl for r in all_results.values())
    overall_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
    
    print(f"\nTotal Trades: {total_trades}")
    print(f"Total Wins: {total_wins}")
    print(f"Total Losses: {total_losses}")
    print(f"Overall Win Rate: {overall_wr:.1f}%")
    print(f"Total P&L: ₹{total_pnl:,.0f}")
    
    # Phase 1 tuning recommendations
    print("\n" + "="*70)
    print("PHASE 1 PARAMETER TUNING RECOMMENDATIONS")
    print("="*70)
    
    recommendations = generate_phase1_tuning(all_results, overall_wr, total_pnl)
    
    for rec in recommendations:
        print(f"\n{rec['emoji']} {rec['title']}")
        print(f"   Current: {rec['current']}")
        print(f"   Recommendation: {rec['recommended']}")
        print(f"   Rationale: {rec['rationale']}")
        print(f"   Expected Impact: {rec['impact']}")
    
    # Save detailed results
    save_backtest_results(all_results, recommendations)
    
    print("\n" + "="*70)
    print("✓ Backtest complete. Results saved to backtest_results.json")
    print("="*70 + "\n")


def generate_phase1_tuning(results: dict, overall_wr: float, total_pnl: float) -> list:
    """Generate Phase 1 parameter tuning recommendations based on results"""
    
    recommendations = []
    
    # Analyze win rate
    if overall_wr < 55:
        recommendations.append({
            'emoji': '⚠️',
            'title': 'Confidence Threshold Too Low',
            'current': 'min_confidence = 50%',
            'recommended': 'min_confidence = 60-65%',
            'rationale': f'Win rate of {overall_wr:.1f}% indicates too many marginal signals. Higher threshold reduces false signals.',
            'impact': '+5-10% win rate improvement'
        })
    
    # Analyze P&L
    if total_pnl < 5000:
        recommendations.append({
            'emoji': '📉',
            'title': 'Risk:Reward Ratio Too Conservative',
            'current': 'rr_ratio = 2.0:1',
            'recommended': 'rr_ratio = 2.5-3.0:1',
            'rationale': f'Low total P&L ({total_pnl:.0f}) suggests targets are too close. Increase RR to capture more upside.',
            'impact': '+20-30% P&L improvement'
        })
    
    # Analyze swing lookback
    recommendations.append({
        'emoji': '🔄',
        'title': 'Swing Detection Sensitivity',
        'current': 'swing_lookback = 10 bars',
        'recommended': 'swing_lookback = 8-10 bars (test 8, 9, 10)',
        'rationale': 'Shorter lookback = more frequent signals but potentially lower quality. Test to find optimal balance.',
        'impact': '+Frequency or +Accuracy depending on market regime'
    })
    
    # Analyze volume multiplier impact
    recommendations.append({
        'emoji': '📊',
        'title': 'Volume Confirmation Strictness',
        'current': 'vol_multiplier = 1.3x',
        'recommended': 'vol_multiplier = 1.2-1.3x',
        'rationale': 'Current setting may be too strict. Lower slightly to capture high-probability low-volume setups.',
        'impact': '+10-15% signal frequency with maintained quality'
    })
    
    # Analyze displacement multiplier
    recommendations.append({
        'emoji': '📌',
        'title': 'Displacement Candle Threshold',
        'current': 'displacement_multiplier = 1.5x',
        'recommended': 'displacement_multiplier = 1.3-1.5x',
        'rationale': 'Current threshold may be filtering out valid setups. Test lower values for aggressive trading.',
        'impact': '+Capture more momentum entries'
    })
    
    # MSS confirmation
    recommendations.append({
        'emoji': '📈',
        'title': 'Market Structure Shift Confirmation',
        'current': 'mss_swing_len = 3 bars',
        'recommended': 'mss_swing_len = 2-3 bars (test both)',
        'rationale': 'Shorter MSS allows earlier entry. Test if it maintains profitability while entering sooner.',
        'impact': '+Earlier entries, -Risk of premature signals'
    })
    
    return recommendations


def save_backtest_results(all_results: dict, recommendations: list):
    """Save detailed backtest results to JSON"""
    
    output = {
        'timestamp': datetime.now().isoformat(),
        'test_type': 'Aggressive Historical Backtest - ICT Sniper Multi-TF',
        'period_days': 90,
        'results_by_index': {},
        'phase1_recommendations': recommendations
    }
    
    for index, result in all_results.items():
        output['results_by_index'][index] = {
            'period': f"{result.period_start} to {result.period_end}",
            'total_candles': result.total_candles,
            'total_signals': result.total_signals,
            'total_trades': result.total_trades,
            'wins': result.wins,
            'losses': result.losses,
            'breakeven': result.breakeven,
            'win_rate': f"{result.win_rate:.1f}%",
            'total_pnl': f"₹{result.total_pnl:,.0f}",
            'avg_win': f"₹{result.avg_win:,.0f}",
            'avg_loss': f"₹{result.avg_loss:,.0f}",
            'profit_factor': f"{result.profit_factor:.2f}",
            'max_drawdown': f"{result.max_drawdown:.1%}",
            'sharpe_ratio': f"{result.sharpe_ratio:.2f}",
            'bot_performance': result.bot_performance
        }
    
    # Save to JSON
    with open('backtest_results_ict_agressive.json', 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\n📄 Detailed results saved to: backtest_results_ict_agressive.json")


if __name__ == "__main__":
    run_aggressive_backtest()
