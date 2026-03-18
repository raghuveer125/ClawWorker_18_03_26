#!/usr/bin/env python3
"""
ICT Sniper Parameter Optimization within Permissive MTF Mode
Tests key parameters to find best configuration for each index
"""

import os
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from livebench.backtesting.backtest import Backtester
from livebench.bots.ict_sniper import ICTConfig, ICTSniperBot
from livebench.bots.base import SharedMemory


def run_single_index_backtest(index: str, vol_mult: float, disp_mult: float, 
                              swing_look: int, mss_len: int) -> dict:
    """Run backtest for single index with custom parameters"""
    
    try:
        backtester = Backtester()
        backtester.mtf_engine.set_mode("permissive")
        
        # Create memory for ICT bot with custom config
        memory = SharedMemory(str(APP_ROOT / "livebench" / "data" / "backtest" / "_ict_backtest_memory"))
        
        # Create ICT bot with custom parameters
        ict_bot = ICTSniperBot(memory)
        ict_bot.config.vol_multiplier = vol_mult
        ict_bot.config.displacement_multiplier = disp_mult
        ict_bot.config.swing_lookback = swing_look
        ict_bot.config.mss_swing_len = mss_len
        
        # Replace bot in backtester (hack for testing)
        for i, bot in enumerate(backtester.bots):
            if bot.name == "ICTSniper":
                backtester.bots[i] = ict_bot
        
        result = backtester.run_backtest(index, days=90, resolution="5")
        
        return {
            "trades": result.total_trades,
            "win_rate": result.win_rate,
            "pnl": result.total_pnl,
            "avg_win": result.avg_win,
            "avg_loss": result.avg_loss,
            "profit_factor": result.profit_factor,
        }
    except Exception as e:
        print(f"Error: {e}")
        return None


def main():
    print(f"""
╔{'='*70}╗
║ ICT SNIPER - PARAMETER OPTIMIZATION (Permissive MTF Mode)             ║
║ Testing vol_multiplier, displacement_multiplier, swing_lookback       ║
╚{'='*70}╝
""")
    
    # Test parameters
    vol_multipliers = [1.0, 1.1, 1.2, 1.3]  # Current Phase 1A: 1.2
    disp_multipliers = [1.2, 1.3, 1.4, 1.5]  # Current Phase 1A: 1.3
    swing_lookbacks = [8, 9, 10, 11]  # Current: 10
    
    # For this test, fix mss_swing_len at 3 (we'll test later if needed)
    mss_swing_len = 3
    
    results = {}
    total_tests = len(vol_multipliers) * len(disp_multipliers) * len(swing_lookbacks)
    test_count = 0
    
    # For brevity, only test vol_mult and disp_mult variations first
    print("Phase 1: Testing vol_multiplier and displacement_multiplier variations\n")
    print(f"{'vol_mult':<10} {'disp_mult':<10} {'swing_look':<10} {'BANKNIFTY WR':<12} {'NIFTY50 WR':<12} {'FINNIFTY WR':<12} {'Avg WR':<10} {'Avg P&L':<10}")
    print(f"{'-'*94}")
    
    results_by_param = {}
    
    for vol_mult in vol_multipliers:
        for disp_mult in disp_multipliers:
            # Test with default swing_lookback=10
            key = f"vol{vol_mult:.1f}_disp{disp_mult:.1f}_swing10"
            results_by_param[key] = {}
            
            pnl_values = []
            wr_values = []
            
            for index in ["BANKNIFTY", "NIFTY50", "FINNIFTY"]:
                result = run_single_index_backtest(index, vol_mult, disp_mult, 10, mss_swing_len)
                if result:
                    results_by_param[key][index] = result
                    wr_values.append(result["win_rate"])
                    pnl_values.append(result["pnl"])
            
            if len(wr_values) == 3:
                avg_wr = sum(wr_values) / 3
                avg_pnl = sum(pnl_values) / 3
                
                print(f"{vol_mult:<10.1f} {disp_mult:<10.1f} {10:<10} {wr_values[0]:<12.1f} {wr_values[1]:<12.1f} {wr_values[2]:<12.1f} {avg_wr:<10.1f} ₹{avg_pnl:<9.0f}")
    
    # Find best by average win rate
    print(f"\n{'-'*94}")
    best_config = max(results_by_param.items(), 
                      key=lambda x: sum(v.get("win_rate", 0) for v in x[1].values()) / len(x[1]))
    
    print(f"\n✓ BEST PARAMETER CONFIG: {best_config[0]}")
    for index, result in best_config[1].items():
        print(f"  {index}: {result['win_rate']:.1f}% WR, ₹{result['pnl']:.0f} P&L")
    
    # Save results
    output_path = APP_ROOT / "optimization_results_params.json"
    with open(output_path, "w") as f:
        json.dump(results_by_param, f, indent=2, default=str)
    
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
