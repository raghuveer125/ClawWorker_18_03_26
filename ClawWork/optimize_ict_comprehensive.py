#!/usr/bin/env python3
"""
Comprehensive ICT Sniper + MTF Optimization
Systematically tests parameter combinations to find optimal settings
"""

import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from livebench.backtesting.backtest import Backtester
from livebench.bots.ict_sniper import ICTSniperBot, ICTConfig
from livebench.bots.base import SharedMemory


def run_optimization():
    """Run comprehensive optimization across MTF modes and ICT parameters"""
    
    print("="*80)
    print("COMPREHENSIVE ICT SNIPER OPTIMIZATION")
    print("="*80)
    print()
    
    # Test configurations
    mtf_modes = ["permissive", "balanced", "strict"]
    
    param_configs = [
        # Phase 1A baseline
        {"name": "Phase1A", "vol": 1.2, "disp": 1.3, "swing": 10, "mss": 3},
        
        # More aggressive (capture more signals)
        {"name": "Aggressive", "vol": 1.1, "disp": 1.2, "swing": 9, "mss": 2},
        
        # More conservative (higher quality)
        {"name": "Conservative", "vol": 1.3, "disp": 1.4, "swing": 11, "mss": 3},
        
        # Phase 1B preview
        {"name": "Phase1B", "vol": 1.2, "disp": 1.3, "swing": 9, "mss": 2},
        
        # Balanced mix
        {"name": "Balanced", "vol": 1.15, "disp": 1.25, "swing": 10, "mss": 3},
    ]
    
    results = []
    
    for mtf_mode in mtf_modes:
        print(f"\n{'='*80}")
        print(f"TESTING MTF MODE: {mtf_mode.upper()}")
        print(f"{'='*80}\n")
        
        for param_config in param_configs:
            config_name = f"{mtf_mode}_{param_config['name']}"
            print(f"\nTesting: {config_name}")
            print(f"  vol_multiplier: {param_config['vol']}")
            print(f"  displacement_multiplier: {param_config['disp']}")
            print(f"  swing_lookback: {param_config['swing']}")
            print(f"  mss_swing_len: {param_config['mss']}")
            
            # Create backtester with custom config
            backtester = Backtester()
            backtester.mtf_engine.set_mode(mtf_mode)
            
            # Create ICT bot with test parameters
            memory = SharedMemory()
            ict_bot = ICTSniperBot(memory)
            ict_bot.config.vol_multiplier = param_config['vol']
            ict_bot.config.displacement_multiplier = param_config['disp']
            ict_bot.config.swing_lookback = param_config['swing']
            ict_bot.config.mss_swing_len = param_config['mss']
            
            # Replace ICT bot in backtester (add it as 6th bot)
            backtester.bots.append(ict_bot)
            
            # Run backtest on all three indices
            indices = ["BANKNIFTY", "NIFTY50", "FINNIFTY"]
            total_trades = 0
            total_wins = 0
            total_losses = 0
            total_pnl = 0
            
            for index in indices:
                try:
                    result = backtester.run_backtest(index, days=90, resolution="5")
                    total_trades += result.total_trades
                    total_wins += result.wins
                    total_losses += result.losses
                    total_pnl += result.total_pnl
                    
                    print(f"    {index}: {result.wins}W/{result.losses}L "
                          f"({result.win_rate:.1f}%) P&L: ₹{result.total_pnl:,.0f}")
                except Exception as e:
                    print(f"    {index}: ERROR - {e}")
            
            win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
            profit_factor = (total_pnl / abs(total_pnl - total_wins)) if total_trades > 0 else 0
            
            result_summary = {
                "config": config_name,
                "mtf_mode": mtf_mode,
                "params": param_config,
                "trades": total_trades,
                "wins": total_wins,
                "losses": total_losses,
                "win_rate": win_rate,
                "pnl": total_pnl,
                "profit_factor": profit_factor,
                "score": win_rate * 0.6 + (total_pnl / 1000) * 0.4  # Composite score
            }
            results.append(result_summary)
            
            print(f"  TOTALS: {total_wins}W/{total_losses}L ({win_rate:.1f}%) "
                  f"P&L: ₹{total_pnl:,.0f} Score: {result_summary['score']:.1f}")
    
    # Sort by composite score
    results.sort(key=lambda x: x['score'], reverse=True)
    
    print("\n" + "="*80)
    print("OPTIMIZATION RESULTS - TOP 5 CONFIGURATIONS")
    print("="*80)
    print()
    
    for i, result in enumerate(results[:5], 1):
        print(f"{i}. {result['config']}")
        print(f"   MTF Mode: {result['mtf_mode']}")
        print(f"   Parameters: vol={result['params']['vol']}, disp={result['params']['disp']}, "
              f"swing={result['params']['swing']}, mss={result['params']['mss']}")
        print(f"   Performance: {result['wins']}W/{result['losses']}L ({result['win_rate']:.1f}%) "
              f"P&L: ₹{result['pnl']:,.0f}")
        print(f"   Composite Score: {result['score']:.1f}")
        print()
    
    # Best configuration
    best = results[0]
    print("="*80)
    print("RECOMMENDED CONFIGURATION (HIGHEST SCORE)")
    print("="*80)
    print()
    print(f"MTF Mode: {best['mtf_mode']}")
    print(f"vol_multiplier: {best['params']['vol']}")
    print(f"displacement_multiplier: {best['params']['disp']}")
    print(f"swing_lookback: {best['params']['swing']}")
    print(f"mss_swing_len: {best['params']['mss']}")
    print()
    print(f"Expected Performance:")
    print(f"  Win Rate: {best['win_rate']:.1f}%")
    print(f"  Total P&L: ₹{best['pnl']:,.0f}")
    print(f"  Total Trades: {best['trades']}")
    print()
    
    # Save results
    import json
    output_file = "ict_optimization_results.json"
    with open(output_file, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "best_config": best,
            "all_results": results
        }, f, indent=2)
    
    print(f"[✓] Results saved to {output_file}")
    print()
    
    return best


if __name__ == "__main__":
    try:
        best_config = run_optimization()
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n[!] Optimization interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Optimization failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
