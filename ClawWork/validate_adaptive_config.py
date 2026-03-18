#!/usr/bin/env python3
"""
Validate adaptive learning results across all indices
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from livebench.backtesting.backtest import Backtester
from livebench.bots.ict_sniper import ICTSniperBot, ICTConfig
from livebench.bots.base import SharedMemory


def validate_adaptive_config():
    """Test the adaptive learning best config across all indices"""
    
    print("=" * 80)
    print("VALIDATION: Adaptive Learning Best Configuration")
    print("=" * 80)
    print()
    print("Configuration (from adaptive learning - Generation 12):")
    print("  vol_multiplier: 1.032")
    print("  displacement_multiplier: 1.284")
    print("  swing_lookback: 10")
    print("  mss_swing_len: 3")
    print("  max_bars_after_sweep: 13")
    print()
    print("=" * 80)
    print()
    
    # Best config from adaptive learning
    config_params = {
        'vol_multiplier': 1.032,
        'displacement_multiplier': 1.284,
        'swing_lookback': 10,
        'mss_swing_len': 3,
        'max_bars_after_sweep': 13
    }
    
    indices = ["BANKNIFTY", "NIFTY50", "FINNIFTY"]
    results = {}
    
    for index in indices:
        print(f"\nTesting {index}...")
        print("-" * 80)
        
        backtester = Backtester()
        backtester.mtf_engine.set_mode("permissive")
        
        # Create ICT bot with adaptive config
        memory = SharedMemory()
        ict_bot = ICTSniperBot(memory)
        ict_bot.config.vol_multiplier = config_params['vol_multiplier']
        ict_bot.config.displacement_multiplier = config_params['displacement_multiplier']
        ict_bot.config.swing_lookback = config_params['swing_lookback']
        ict_bot.config.mss_swing_len = config_params['mss_swing_len']
        ict_bot.config.max_bars_after_sweep = config_params['max_bars_after_sweep']
        
        backtester.bots.append(ict_bot)
        
        # Run backtest
        result = backtester.run_backtest(index, days=90, resolution="5")
        
        results[index] = {
            'wins': result.wins,
            'losses': result.losses,
            'total_trades': result.total_trades,
            'win_rate': result.win_rate,
            'pnl': result.total_pnl
        }
        
        print(f"{index}: {result.wins}W/{result.losses}L ({result.win_rate:.2f}%) P&L: ₹{result.total_pnl:,.0f}")
    
    # Calculate overall performance
    total_wins = sum(r['wins'] for r in results.values())
    total_losses = sum(r['losses'] for r in results.values())
    total_trades = sum(r['total_trades'] for r in results.values())
    total_pnl = sum(r['pnl'] for r in results.values())
    overall_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
    
    print("\n" + "=" * 80)
    print("OVERALL PERFORMANCE (3 indices, 90 days)")
    print("=" * 80)
    print(f"Total: {total_wins}W/{total_losses}L ({overall_wr:.2f}%)")
    print(f"Total P&L: ₹{total_pnl:,.0f}")
    print(f"Avg P&L per Index: ₹{total_pnl/3:,.0f}")
    print()
    
    # Comparison with Phase 1B
    print("COMPARISON:")
    print("-" * 80)
    print("Phase 1B (permissive):      53.7% WR, ₹25,647 P&L")
    print(f"Adaptive Learning (Gen 12): {overall_wr:.1f}% WR, ₹{total_pnl:,.0f} P&L")
    print()
    
    if overall_wr > 53.7:
        print("✓✓ ADAPTIVE LEARNING WINS - Deploy this configuration!")
    else:
        print("⚠ Phase 1B remains best - Keep previous configuration")
    
    print()
    return overall_wr >= 53.7


if __name__ == "__main__":
    try:
        success = validate_adaptive_config()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Validation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
