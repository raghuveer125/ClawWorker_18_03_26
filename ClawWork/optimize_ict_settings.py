#!/usr/bin/env python3
"""
ICT Sniper Multi-Timeframe - Optimization Script
Tests different MTF modes and parameter settings to find optimal configuration

Runs multiple backtests with different configurations and compares results.
"""

import os
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))
APP_ROOT = Path(__file__).resolve().parent

# Add project to path
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from livebench.backtesting.backtest import Backtester
from livebench.bots.ict_sniper import ICTConfig


def run_backtest_with_config(mtf_mode: str, confidence_threshold: int) -> dict:
    """Run backtest with specific configuration"""
    
    print(f"\n{'='*70}")
    print(f"Testing: MTF Mode = {mtf_mode.upper()}, Min Confidence = {confidence_threshold}%")
    print(f"{'='*70}\n")
    
    # Create backtester
    backtester = Backtester()
    
    # Set MTF mode
    backtester.mtf_engine.set_mode(mtf_mode)
    backtester.mtf_engine.config["only_block_strong_trends"] = True
    
    # Override confidence threshold in backtest
    # Note: We can't change ICTConfig here because it's already instantiated
    # Instead we'll track the impact of different MTF modes
    
    results = {
        "config": {
            "mtf_mode": mtf_mode,
            "confidence_threshold": confidence_threshold,
        },
        "by_index": {},
        "aggregate": {}
    }
    
    for index in ["BANKNIFTY", "NIFTY50", "FINNIFTY"]:
        print(f"\nTesting {index}...")
        result = backtester.run_backtest(index, days=90)
        
        results["by_index"][index] = {
            "total_trades": result.total_trades,
            "wins": result.wins,
            "losses": result.losses,
            "win_rate": result.win_rate,
            "total_pnl": result.total_pnl,
            "avg_win": result.avg_win,
            "avg_loss": result.avg_loss,
            "profit_factor": result.profit_factor,
        }
        
        print(f"  Trades: {result.total_trades} | Win Rate: {result.win_rate:.1f}% | P&L: ₹{result.total_pnl:.0f}")
    
    # Calculate aggregate
    total_trades = sum(r["total_trades"] for r in results["by_index"].values())
    total_wins = sum(r["wins"] for r in results["by_index"].values())
    total_losses = sum(r["losses"] for r in results["by_index"].values())
    total_pnl = sum(r["total_pnl"] for r in results["by_index"].values())
    
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    
    results["aggregate"] = {
        "total_trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    }
    
    return results


def main():
    """Run optimization tests"""
    
    print(f"""
╔{'='*68}╗
║ ICT SNIPER - MULTI-TIMEFRAME OPTIMIZATION                            ║
║ Testing MTF modes for best risk-adjusted returns                      ║
╚{'='*68}╝

Testing Strategy:
- MTF Modes: permissive, balanced, strict
- Confidence threshold: 50% (baseline)
- Each config: 3 indices × 90 days = full dataset

Expected behavior:
  permissive: Block 0%, highest volume, moderate win rate
  balanced:   Block strong trends only, balanced approach
  strict:     Block all counter-trend, lowest volume, highest quality

""")
    
    results_all = {}
    
    # Test all modes
    modes = ["permissive", "balanced", "strict"]
    confidence_threshold = 50
    
    for mode in modes:
        try:
            results = run_backtest_with_config(mode, confidence_threshold)
            results_all[f"{mode}_50"] = results
            
            # Print summary
            agg = results["aggregate"]
            print(f"\n{'─'*70}")
            print(f"Summary: {mode.upper()} (Conf: {confidence_threshold}%)")
            print(f"  Total Trades: {agg['total_trades']}")
            print(f"  Win Rate: {agg['win_rate']:.1f}%")
            print(f"  Total P&L: ₹{agg['total_pnl']:.0f}")
            print(f"  Avg Trade: ₹{(agg['total_pnl']/agg['total_trades']):.0f}" if agg['total_trades'] > 0 else "  N/A")
            
        except Exception as e:
            print(f"ERROR testing {mode}: {e}")
            import traceback
            traceback.print_exc()
    
    # Save results
    output_path = APP_ROOT / "optimization_results_mtf.json"
    with open(output_path, "w") as f:
        json.dump(results_all, f, indent=2, default=str)
    
    print(f"\n\n{'='*70}")
    print("OPTIMIZATION COMPLETE")
    print(f"Results saved to: {output_path}")
    print(f"{'='*70}\n")
    
    # Print final comparison
    print("\n📊 FINAL COMPARISON:\n")
    print(f"{'MTF Mode':<15} {'Trades':<10} {'Win Rate':<12} {'P&L':<15}")
    print(f"{'-'*52}")
    
    for key, results in sorted(results_all.items()):
        agg = results["aggregate"]
        print(f"{key:<15} {agg['total_trades']:<10} {agg['win_rate']:.1f}%{'':<7} ₹{agg['total_pnl']:.0f}")
    
    # Recommend best
    print(f"\n{'─'*52}")
    best_key = max(results_all.keys(), key=lambda k: (
        results_all[k]["aggregate"]["win_rate"],
        results_all[k]["aggregate"]["total_pnl"]
    ))
    best = results_all[best_key]
    print(f"\n✓ RECOMMENDED: {best_key.upper()}")
    print(f"  Win Rate: {best['aggregate']['win_rate']:.1f}%")
    print(f"  P&L: ₹{best['aggregate']['total_pnl']:.0f}")
    print(f"  Trades: {best['aggregate']['total_trades']}")


if __name__ == "__main__":
    main()
