#!/usr/bin/env python3
"""
Adaptive Learning Optimizer for ICT Sniper
Iteratively optimizes parameters based on trade-by-trade analysis

Strategy:
1. Start with baseline config
2. Execute trade, analyze outcome
3. If WIN: Record settings, continue
4. If LOSS: Analyze failure, adjust parameters near winning config
5. Iterate until convergence or target win rate achieved

Target: 97% win rate through adaptive parameter tuning
"""

import sys
import os
import json
import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
import statistics

sys.path.insert(0, str(Path(__file__).parent))

from livebench.backtesting.backtest import Backtester
from livebench.bots.ict_sniper import ICTSniperBot, ICTConfig
from livebench.bots.base import SharedMemory


@dataclass
class ParameterState:
    """Tracks parameter configuration and its performance"""
    vol_multiplier: float
    displacement_multiplier: float
    swing_lookback: int
    mss_swing_len: int
    max_bars_after_sweep: int
    
    # Performance metrics
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    
    # Learning metadata
    generation: int = 0
    parent_config: Optional[str] = None
    failure_reasons: List[str] = None

    def __post_init__(self):
        if self.failure_reasons is None:
            self.failure_reasons = []


class AdaptiveLearningOptimizer:
    """
    Trade-by-trade adaptive optimizer
    Learns optimal parameters through iterative backtesting
    """
    
    def __init__(self, target_win_rate: float = 97.0):
        self.target_win_rate = target_win_rate
        self.max_iterations = 1000  # Safety limit
        self.convergence_trades = 100  # Min trades to verify convergence
        
        # Learning state
        self.winning_configs: List[ParameterState] = []
        self.current_best: Optional[ParameterState] = None
        self.generation = 0
        
        # Knowledge base
        self.trade_history: List[Dict] = []
        self.failure_patterns: Dict[str, int] = {}
        
        # Learning rate (how aggressively to adjust parameters)
        self.learning_rate = 0.1
        
        # Output directory
        self.output_dir = Path("adaptive_optimization_output")
        self.output_dir.mkdir(exist_ok=True)
        
    def analyze_failure(self, trade: Dict, config: ParameterState) -> List[str]:
        """
        Analyze why a trade failed and suggest parameter adjustments
        
        Returns:
            List of failure reasons/patterns identified
        """
        reasons = []
        
        # Analyze entry quality
        if trade.get("confidence", 0) < 70:
            reasons.append("low_confidence")
            
        # Analyze setup timing
        if trade.get("bars_in_setup", 0) > config.max_bars_after_sweep:
            reasons.append("setup_expired")
            
        # Analyze market structure
        if abs(trade.get("pnl_pct", 0)) < 0.5:
            reasons.append("small_move_chop")
        elif abs(trade.get("pnl_pct", 0)) > 3:
            reasons.append("strong_counter_trend")
            
        # Analyze FVG quality
        if trade.get("fvg_size", 0) > 2.5:
            reasons.append("fvg_too_large")
            
        # Analyze volume confirmation
        if not trade.get("volume_confirmed", True):
            reasons.append("no_volume_spike")
            
        # Analyze displacement
        if not trade.get("displacement_confirmed", True):
            reasons.append("no_displacement")
        
        # Track failure patterns
        for reason in reasons:
            self.failure_patterns[reason] = self.failure_patterns.get(reason, 0) + 1
            
        return reasons
    
    def adjust_parameters(self, config: ParameterState, failure_reasons: List[str]) -> ParameterState:
        """
        Adjust parameters based on failure analysis
        Keeps adjustments close to winning configurations
        """
        new_config = copy.deepcopy(config)
        new_config.generation = self.generation + 1
        new_config.parent_config = f"gen{config.generation}"
        
        # Get average of winning configs (if any)
        if self.winning_configs:
            avg_vol = statistics.mean([c.vol_multiplier for c in self.winning_configs[-5:]])
            avg_disp = statistics.mean([c.displacement_multiplier for c in self.winning_configs[-5:]])
            avg_swing = statistics.mean([c.swing_lookback for c in self.winning_configs[-5:]])
            avg_mss = statistics.mean([c.mss_swing_len for c in self.winning_configs[-5:]])
        else:
            # Use current best or defaults
            avg_vol = config.vol_multiplier
            avg_disp = config.displacement_multiplier
            avg_swing = config.swing_lookback
            avg_mss = config.mss_swing_len
        
        # Adjust based on failure patterns
        if "low_confidence" in failure_reasons or "no_volume_spike" in failure_reasons:
            # Relax volume requirements slightly
            new_config.vol_multiplier = max(1.0, new_config.vol_multiplier - self.learning_rate)
            
        if "strong_counter_trend" in failure_reasons:
            # Require stronger displacement confirmation
            new_config.displacement_multiplier = min(2.0, new_config.displacement_multiplier + self.learning_rate)
            
        if "setup_expired" in failure_reasons:
            # Reduce setup expiry window
            new_config.max_bars_after_sweep = max(5, new_config.max_bars_after_sweep - 1)
            
        if "fvg_too_large" in failure_reasons:
            # Already handled in ICTConfig.max_fvg_size
            pass
            
        if "small_move_chop" in failure_reasons:
            # Need stronger structure confirmation
            new_config.swing_lookback = min(15, new_config.swing_lookback + 1)
            new_config.mss_swing_len = min(5, new_config.mss_swing_len + 1)
            
        # Blend with winning average (80% new, 20% winning avg)
        if self.winning_configs:
            new_config.vol_multiplier = new_config.vol_multiplier * 0.8 + avg_vol * 0.2
            new_config.displacement_multiplier = new_config.displacement_multiplier * 0.8 + avg_disp * 0.2
            new_config.swing_lookback = int(new_config.swing_lookback * 0.8 + avg_swing * 0.2)
            new_config.mss_swing_len = int(new_config.mss_swing_len * 0.8 + avg_mss * 0.2)
        
        return new_config
    
    def run_adaptive_optimization(self, index: str = "BANKNIFTY", days: int = 90) -> ParameterState:
        """
        Main optimization loop: iteratively improve parameters trade-by-trade
        """
        print("=" * 80)
        print(f"ADAPTIVE LEARNING OPTIMIZER - Target: {self.target_win_rate}% Win Rate")
        print("=" * 80)
        print(f"Index: {index} | Days: {days}")
        print(f"Strategy: Analyze each trade, adapt parameters on losses")
        print("=" * 80)
        print()
        
        # Start with Phase 1B optimized config
        current_config = ParameterState(
            vol_multiplier=1.2,
            displacement_multiplier=1.3,
            swing_lookback=9,
            mss_swing_len=2,
            max_bars_after_sweep=10
        )
        
        best_win_rate = 0.0
        iterations = 0
        consecutive_improvements = 0
        
        while iterations < self.max_iterations:
            iterations += 1
            self.generation += 1
            
            print(f"\n{'='*80}")
            print(f"GENERATION {self.generation} - Iteration {iterations}")
            print(f"{'='*80}")
            print(f"Config: vol={current_config.vol_multiplier:.2f}, "
                  f"disp={current_config.displacement_multiplier:.2f}, "
                  f"swing={current_config.swing_lookback}, "
                  f"mss={current_config.mss_swing_len}")
            
            # Run backtest with current config
            result = self._run_backtest_with_config(index, days, current_config)
            
            current_win_rate = result['win_rate']
            current_config.wins = result['wins']
            current_config.losses = result['losses']
            current_config.total_pnl = result['pnl']
            current_config.win_rate = current_win_rate
            
            print(f"\nResults: {result['wins']}W/{result['losses']}L ({current_win_rate:.2f}%) "
                  f"P&L: ₹{result['pnl']:,.0f}")
            
            # Check if this is better than best so far
            if current_win_rate > best_win_rate:
                best_win_rate = current_win_rate
                self.current_best = copy.deepcopy(current_config)
                consecutive_improvements += 1
                print(f"✓ NEW BEST: {current_win_rate:.2f}% (improvement #{consecutive_improvements})")
                
                # Record as winning config
                self.winning_configs.append(copy.deepcopy(current_config))
                
                # Check if target achieved
                if current_win_rate >= self.target_win_rate and result['total_trades'] >= self.convergence_trades:
                    print(f"\n🎯 TARGET ACHIEVED: {current_win_rate:.2f}% >= {self.target_win_rate}%")
                    break
            else:
                consecutive_improvements = 0
                print(f"⚠ No improvement: {current_win_rate:.2f}% vs best {best_win_rate:.2f}%")
            
            # Analyze recent losses and adapt
            recent_losses = [t for t in result['trades'] if t['outcome'] == 'LOSS']
            if recent_losses:
                print(f"\nAnalyzing {len(recent_losses)} losses...")
                
                # Aggregate failure reasons
                all_reasons = []
                for trade in recent_losses[:10]:  # Analyze last 10 losses
                    reasons = self.analyze_failure(trade, current_config)
                    all_reasons.extend(reasons)
                
                # Most common failure patterns
                if all_reasons:
                    from collections import Counter
                    top_failures = Counter(all_reasons).most_common(3)
                    print(f"Top failure patterns: {top_failures}")
                    
                    # Adjust parameters
                    current_config = self.adjust_parameters(current_config, all_reasons)
                    print(f"Adjusted config: vol={current_config.vol_multiplier:.2f}, "
                          f"disp={current_config.displacement_multiplier:.2f}, "
                          f"swing={current_config.swing_lookback}, "
                          f"mss={current_config.mss_swing_len}")
            
            # Convergence check: if no improvement in 5 iterations, try random exploration
            if consecutive_improvements == 0 and iterations % 5 == 0:
                print("\n🔄 No improvements - exploring parameter space...")
                current_config = self._explore_parameter_space(current_config)
            
            # Progress report every 10 iterations
            if iterations % 10 == 0:
                self._save_progress(current_config, best_win_rate, iterations)
            
            # Safety: if convergence detected (no improvement in 20 iterations), stop
            if iterations >= 20 and best_win_rate < 60:
                print(f"\n⚠ Early convergence detected at {best_win_rate:.2f}%")
                print("Consider adjusting strategy or accepting realistic win rate targets")
                break
        
        # Final report
        print("\n" + "=" * 80)
        print("OPTIMIZATION COMPLETE")
        print("=" * 80)
        print(f"Iterations: {iterations}")
        print(f"Best Win Rate Achieved: {best_win_rate:.2f}%")
        print(f"Target: {self.target_win_rate}%")
        print()
        
        if self.current_best:
            print("BEST CONFIGURATION:")
            print(f"  vol_multiplier: {self.current_best.vol_multiplier:.2f}")
            print(f"  displacement_multiplier: {self.current_best.displacement_multiplier:.2f}")
            print(f"  swing_lookback: {self.current_best.swing_lookback}")
            print(f"  mss_swing_len: {self.current_best.mss_swing_len}")
            print(f"  max_bars_after_sweep: {self.current_best.max_bars_after_sweep}")
            print(f"\n  Performance: {self.current_best.wins}W/{self.current_best.losses}L ({self.current_best.win_rate:.2f}%)")
            print(f"  P&L: ₹{self.current_best.total_pnl:,.0f}")
        
        # Save final results
        self._save_final_results(best_win_rate, iterations)
        
        return self.current_best or current_config
    
    def _run_backtest_with_config(self, index: str, days: int, config: ParameterState) -> Dict:
        """Run backtest with specific configuration and return detailed results"""
        backtester = Backtester()
        backtester.mtf_engine.set_mode("permissive")
        
        # Create ICT bot with config
        memory = SharedMemory()
        ict_bot = ICTSniperBot(memory)
        ict_bot.config.vol_multiplier = config.vol_multiplier
        ict_bot.config.displacement_multiplier = config.displacement_multiplier
        ict_bot.config.swing_lookback = config.swing_lookback
        ict_bot.config.mss_swing_len = config.mss_swing_len
        ict_bot.config.max_bars_after_sweep = config.max_bars_after_sweep
        
        backtester.bots.append(ict_bot)
        
        # Run backtest
        result = backtester.run_backtest(index, days=days, resolution="5")
        
        return {
            'wins': result.wins,
            'losses': result.losses,
            'total_trades': result.total_trades,
            'win_rate': result.win_rate,
            'pnl': result.total_pnl,
            'trades': result.trades
        }
    
    def _explore_parameter_space(self, base_config: ParameterState) -> ParameterState:
        """Explore new parameter combinations when stuck"""
        import random
        
        new_config = copy.deepcopy(base_config)
        
        # Random exploration within reasonable bounds
        new_config.vol_multiplier = round(random.uniform(1.0, 1.5), 2)
        new_config.displacement_multiplier = round(random.uniform(1.1, 1.6), 2)
        new_config.swing_lookback = random.randint(7, 12)
        new_config.mss_swing_len = random.randint(2, 4)
        new_config.max_bars_after_sweep = random.randint(8, 15)
        
        return new_config
    
    def _save_progress(self, config: ParameterState, best_win_rate: float, iteration: int):
        """Save optimization progress"""
        progress = {
            "iteration": iteration,
            "generation": self.generation,
            "best_win_rate": best_win_rate,
            "current_config": asdict(config),
            "winning_configs_count": len(self.winning_configs),
            "failure_patterns": self.failure_patterns
        }
        
        output_file = self.output_dir / f"progress_iter_{iteration}.json"
        with open(output_file, 'w') as f:
            json.dump(progress, f, indent=2)
    
    def _save_final_results(self, best_win_rate: float, total_iterations: int):
        """Save final optimization results"""
        results = {
            "timestamp": datetime.now().isoformat(),
            "target_win_rate": self.target_win_rate,
            "achieved_win_rate": best_win_rate,
            "total_iterations": total_iterations,
            "best_config": asdict(self.current_best) if self.current_best else None,
            "winning_configs": [asdict(c) for c in self.winning_configs],
            "failure_patterns": self.failure_patterns,
            "convergence": best_win_rate >= self.target_win_rate
        }
        
        output_file = self.output_dir / "final_results.json"
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\n[✓] Results saved to {output_file}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Adaptive Learning Optimizer for ICT Sniper")
    parser.add_argument("--target", type=float, default=97.0, help="Target win rate (default: 97.0)")
    parser.add_argument("--index", type=str, default="BANKNIFTY", help="Index to optimize (default: BANKNIFTY)")
    parser.add_argument("--days", type=int, default=90, help="Backtest days (default: 90)")
    
    args = parser.parse_args()
    
    try:
        optimizer = AdaptiveLearningOptimizer(target_win_rate=args.target)
        best_config = optimizer.run_adaptive_optimization(index=args.index, days=args.days)
        
        print("\n" + "=" * 80)
        print("OPTIMIZATION COMPLETE - Ready to deploy!")
        print("=" * 80)
        
        sys.exit(0)
        
    except KeyboardInterrupt:
        print("\n[!] Optimization interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Optimization failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
