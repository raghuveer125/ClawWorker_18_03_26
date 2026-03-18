"""
AI-Powered Self-Learning Optimizer

Uses OpenAI to analyze trading logs and automatically adjust parameters
to improve performance. Runs continuously and learns from outcomes.

Features:
- Reads trading logs and identifies patterns
- Analyzes rejection reasons and adjusts thresholds
- Learns from winning/losing trades
- Suggests and applies parameter optimizations
"""

import os
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
import logging

LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = LIVEBENCH_ROOT.parent
REPO_ROOT = APP_ROOT.parent
DEFAULT_LOG_DIR = APP_ROOT / "logs"
DEFAULT_CONFIG_DIR = LIVEBENCH_ROOT / "data"

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - depends on runtime env
    load_dotenv = None

if load_dotenv is not None:
    for env_path in (APP_ROOT / ".env", REPO_ROOT / ".env"):
        if env_path.exists():
            load_dotenv(env_path)
            break

logger = logging.getLogger(__name__)

SUPPORTED_RUNTIME_PARAMETERS = {
    "min_signal_strength": {"type": "float", "min": 30.0, "max": 70.0},
    "min_confidence": {"type": "float", "min": 45.0, "max": 85.0},
    "min_bots_required": {"type": "int", "min": 1, "max": 4},
    "high_conviction_threshold": {"type": "float", "min": 60.0, "max": 85.0},
    "mtf_mode": {"type": "choice", "choices": {"strict", "balanced", "permissive"}},
    "capital_preservation_mode": {"type": "bool"},
}

# Try to import OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI not installed. Run: pip install openai")


@dataclass
class OptimizationSuggestion:
    """A suggested parameter change"""
    parameter: str
    current_value: Any
    suggested_value: Any
    reason: str
    confidence: float  # 0-100
    impact: str  # LOW, MEDIUM, HIGH


@dataclass
class LogAnalysis:
    """Analysis of recent logs"""
    total_signals: int
    blocked_signals: int
    executed_trades: int
    rejection_reasons: Dict[str, int]
    bot_performance: Dict[str, Dict]
    suggestions: List[OptimizationSuggestion]
    timestamp: str


class AIOptimizer:
    """
    AI-Powered Trading Parameter Optimizer

    Analyzes logs, identifies bottlenecks, and suggests/applies improvements.
    """

    def __init__(
        self,
        log_dir: str = None,
        config_dir: str = None,
        auto_apply: bool = False,  # If True, automatically apply suggestions
        min_confidence: float = 80,  # Only apply suggestions above this confidence
    ):
        self.log_dir = Path(log_dir or DEFAULT_LOG_DIR)
        self.config_dir = Path(config_dir or DEFAULT_CONFIG_DIR)
        self.auto_apply = auto_apply
        self.min_confidence = min_confidence
        self.runtime_overrides_file = self.config_dir / "bots" / "ensemble_runtime_overrides.json"

        # OpenAI client
        self.client = None
        if OPENAI_AVAILABLE:
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key:
                self.client = OpenAI(api_key=api_key)

        # Optimization history
        self.history_file = self.config_dir / "optimization_history.json"
        self.history: List[Dict] = self._load_history()

        # Current parameters (will be loaded from configs)
        self.current_params = self._load_current_params()

    def _default_runtime_parameters(self) -> Dict[str, Any]:
        """Load the current ensemble defaults for runtime-tunable thresholds."""
        try:
            from .ensemble import EnsembleConfig

            config = EnsembleConfig()
            return {
                "min_signal_strength": config.min_signal_strength,
                "min_confidence": config.min_confidence,
                "min_bots_required": config.min_bots_required,
                "high_conviction_threshold": config.high_conviction_threshold,
                "mtf_mode": config.mtf_mode,
                "capital_preservation_mode": True,
            }
        except Exception:
            return {
                "min_signal_strength": 40.0,
                "min_confidence": 55.0,
                "min_bots_required": 2,
                "high_conviction_threshold": 70.0,
                "mtf_mode": "balanced",
                "capital_preservation_mode": True,
            }

    def _sanitize_parameter_value(self, parameter: str, value: Any) -> Optional[Any]:
        spec = SUPPORTED_RUNTIME_PARAMETERS.get(parameter)
        if spec is None:
            return None

        param_type = spec["type"]
        if param_type == "choice":
            normalized = str(value).strip().lower()
            return normalized if normalized in spec["choices"] else None
        if param_type == "bool":
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
            return None

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None

        numeric = max(spec["min"], min(spec["max"], numeric))
        if param_type == "int":
            return int(round(numeric))
        return round(numeric, 2)

    def _load_runtime_overrides(self) -> Dict[str, Any]:
        if not self.runtime_overrides_file.exists():
            return {}
        try:
            with open(self.runtime_overrides_file, "r") as f:
                raw = json.load(f)
        except Exception:
            return {}

        if not isinstance(raw, dict):
            return {}

        cleaned: Dict[str, Any] = {}
        for key, value in raw.items():
            normalized = self._sanitize_parameter_value(key, value)
            if normalized is not None:
                cleaned[key] = normalized
        return cleaned

    def _save_runtime_overrides(self, overrides: Dict[str, Any]) -> None:
        self.runtime_overrides_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.runtime_overrides_file, "w") as f:
            json.dump(overrides, f, indent=2)

    def _load_current_params(self) -> Dict[str, Any]:
        current = self._default_runtime_parameters()
        current.update(self._load_runtime_overrides())
        return current

    def _load_history(self) -> List[Dict]:
        """Load optimization history"""
        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    return json.load(f)
            except:
                return []
        return []

    def _save_history(self):
        """Save optimization history"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.history_file, "w") as f:
            json.dump(self.history[-100:], f, indent=2)  # Keep last 100

    def read_recent_logs(self, hours: int = 1) -> str:
        """Read logs from the last N hours"""
        log_content = []

        # Read API log
        api_log = self.log_dir / "api.log"
        if api_log.exists():
            try:
                with open(api_log, "r") as f:
                    lines = f.readlines()
                    # Get last 500 lines or last N hours
                    log_content.extend(lines[-500:])
            except Exception as e:
                logger.error(f"Error reading api.log: {e}")

        return "".join(log_content)

    def parse_log_patterns(self, log_content: str) -> Dict:
        """Parse logs to extract patterns and statistics"""
        patterns = {
            "blocked_choppy": len(re.findall(r"BLOCKED.*choppy", log_content)),
            "blocked_mtf": len(re.findall(r"\[MTF\] BLOCKED", log_content)),
            "blocked_capital": len(re.findall(r"\[Capital Preservation\].*found", log_content)),
            "insufficient_bots": len(re.findall(r"Need \d+ bots, only have \d+", log_content)),
            "low_confidence": len(re.findall(r"Low confidence", log_content)),
            "signals_generated": len(re.findall(r"\[Ensemble\].*signals,.*quality", log_content)),
            "trades_executed": len(re.findall(r"EXECUTED", log_content)),
            "caution_conditions": len(re.findall(r"CAUTION conditions", log_content)),
        }

        # Extract bot confidence levels
        bot_confidences = {}
        for match in re.finditer(r"(\w+): (STRONG_SELL|STRONG_BUY|SELL|BUY) @ (\d+)%", log_content):
            bot_name = match.group(1)
            confidence = int(match.group(3))
            if bot_name not in bot_confidences:
                bot_confidences[bot_name] = []
            bot_confidences[bot_name].append(confidence)

        # Calculate averages
        for bot, confs in bot_confidences.items():
            patterns[f"avg_conf_{bot}"] = sum(confs) / len(confs) if confs else 0

        return patterns

    def analyze_with_ai(self, log_content: str, patterns: Dict) -> List[OptimizationSuggestion]:
        """Use OpenAI to analyze logs and suggest improvements"""
        self.current_params = self._load_current_params()

        if not self.client:
            logger.warning("OpenAI client not available, using rule-based analysis")
            return self._rule_based_analysis(patterns)

        prompt = f"""You are an expert algorithmic trading system optimizer with 65+ years of institutional trading experience.

Analyze these trading system logs and suggest parameter adjustments.

CURRENT STATISTICS:
{json.dumps(patterns, indent=2)}

RECENT LOG EXCERPT:
{log_content[-3000:]}

CURRENT PARAMETERS:
- min_signal_strength: {self.current_params['min_signal_strength']}% (minimum individual bot confidence)
- min_confidence: {self.current_params['min_confidence']}% (minimum weighted consensus confidence)
- min_bots_required: {self.current_params['min_bots_required']} (minimum agreeing bots)
- high_conviction_threshold: {self.current_params['high_conviction_threshold']}% (for capital preservation)
- mtf_mode: {self.current_params['mtf_mode']}
- capital_preservation_mode: {self.current_params['capital_preservation_mode']}

RULES:
1. If "blocked_mtf" is high but signals look good, consider preserving high-conviction signals
2. If "insufficient_bots" is high, consider lowering min_bots_required or min_signal_strength
3. If bot average confidences are consistently below thresholds, adjust thresholds to match
4. Never suggest changes that would compromise risk management
5. Quality over quantity - better to miss trades than take bad ones
6. Only suggest supported parameters from this list:
   min_signal_strength, min_confidence, min_bots_required,
   high_conviction_threshold, mtf_mode, capital_preservation_mode

Respond in JSON format:
{{
    "analysis": "Brief analysis of the issues",
    "suggestions": [
        {{
            "parameter": "parameter_name",
            "current_value": current,
            "suggested_value": suggested,
            "reason": "why this change helps",
            "confidence": 0-100,
            "impact": "LOW|MEDIUM|HIGH"
        }}
    ]
}}
"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.3,
            )

            result = json.loads(response.choices[0].message.content)

            suggestions = []
            for s in result.get("suggestions", []):
                suggestions.append(OptimizationSuggestion(
                    parameter=s["parameter"],
                    current_value=s["current_value"],
                    suggested_value=s["suggested_value"],
                    reason=s["reason"],
                    confidence=s["confidence"],
                    impact=s["impact"],
                ))

            logger.info(f"AI Analysis: {result.get('analysis', 'No analysis')}")
            return suggestions

        except Exception as e:
            logger.error(f"OpenAI analysis failed: {e}")
            return self._rule_based_analysis(patterns)

    def _rule_based_analysis(self, patterns: Dict) -> List[OptimizationSuggestion]:
        """Fallback rule-based analysis when AI is unavailable"""
        suggestions = []
        current = self._load_current_params()

        def add_suggestion(parameter: str, suggested_value: Any, reason: str, confidence: float, impact: str):
            if any(existing.parameter == parameter for existing in suggestions):
                return
            suggestions.append(OptimizationSuggestion(
                parameter=parameter,
                current_value=current.get(parameter),
                suggested_value=suggested_value,
                reason=reason,
                confidence=confidence,
                impact=impact,
            ))

        # Rule 1: Too many MTF blocks
        if patterns.get("blocked_mtf", 0) > 10 and current.get("mtf_mode") != "permissive":
            add_suggestion(
                "mtf_mode",
                "permissive",
                f"MTF blocked {patterns['blocked_mtf']} signals - move to permissive mode for strong setups",
                85,
                "MEDIUM",
            )

        # Rule 2: Insufficient bots frequently
        if patterns.get("insufficient_bots", 0) > 5 and float(current.get("min_signal_strength", 40)) > 35:
            add_suggestion(
                "min_signal_strength",
                max(35.0, float(current["min_signal_strength"]) - 5.0),
                f"Insufficient bots {patterns['insufficient_bots']} times - lower individual bot threshold slightly",
                72,
                "MEDIUM",
            )

        # Rule 3: Capital preservation blocking
        if patterns.get("blocked_capital", 0) > 5:
            # Check average bot confidences
            avg_trend = patterns.get("avg_conf_TrendFollower", 0)
            if avg_trend > 0 and avg_trend < float(current.get("high_conviction_threshold", 70)):
                add_suggestion(
                    "high_conviction_threshold",
                    max(65.0, float(current["high_conviction_threshold"]) - 5.0),
                    f"Capital preservation blocked trades while TrendFollower averaged {avg_trend:.0f}% confidence",
                    75,
                    "HIGH",
                )

        # Rule 4: No trades executed despite signals
        if patterns.get("signals_generated", 0) > 10 and patterns.get("trades_executed", 0) == 0:
            if patterns.get("low_confidence", 0) > 5 and float(current.get("min_confidence", 55)) > 50:
                add_suggestion(
                    "min_confidence",
                    max(50.0, float(current["min_confidence"]) - 5.0),
                    "Many signals are generated but filtered by consensus confidence before execution",
                    80,
                    "HIGH",
                )
            elif int(current.get("min_bots_required", 2)) > 1:
                add_suggestion(
                    "min_bots_required",
                    max(1, int(current["min_bots_required"]) - 1),
                    "Signals exist, but current bot agreement requirements are stricter than observed participation",
                    68,
                    "MEDIUM",
                )

        return suggestions

    def analyze(self) -> LogAnalysis:
        """Run full analysis and return results"""
        self.current_params = self._load_current_params()

        # Read logs
        log_content = self.read_recent_logs(hours=1)

        # Parse patterns
        patterns = self.parse_log_patterns(log_content)

        # Get AI suggestions
        suggestions = self.analyze_with_ai(log_content, patterns)

        analysis = LogAnalysis(
            total_signals=patterns.get("signals_generated", 0),
            blocked_signals=sum([
                patterns.get("blocked_choppy", 0),
                patterns.get("blocked_mtf", 0),
                patterns.get("blocked_capital", 0),
            ]),
            executed_trades=patterns.get("trades_executed", 0),
            rejection_reasons={
                "choppy_market": patterns.get("blocked_choppy", 0),
                "mtf_filter": patterns.get("blocked_mtf", 0),
                "capital_preservation": patterns.get("blocked_capital", 0),
                "insufficient_bots": patterns.get("insufficient_bots", 0),
            },
            bot_performance={
                bot: {"avg_confidence": patterns.get(f"avg_conf_{bot}", 0)}
                for bot in ["TrendFollower", "OIAnalyst", "VolatilityTrader", "ReversalHunter"]
                if f"avg_conf_{bot}" in patterns
            },
            suggestions=suggestions,
            timestamp=datetime.now().isoformat(),
        )

        # Save to history
        self.history.append(asdict(analysis))
        self._save_history()

        return analysis

    def apply_suggestion(self, suggestion: OptimizationSuggestion) -> bool:
        """Persist supported ensemble runtime overrides for later reload."""
        normalized = self._sanitize_parameter_value(suggestion.parameter, suggestion.suggested_value)
        if normalized is None:
            logger.warning(
                "Unsupported optimizer suggestion skipped: %s=%r",
                suggestion.parameter,
                suggestion.suggested_value,
            )
            return False

        overrides = self._load_runtime_overrides()
        current_value = overrides.get(
            suggestion.parameter,
            self._default_runtime_parameters().get(suggestion.parameter),
        )
        if current_value == normalized:
            self.current_params = self._load_current_params()
            return True

        overrides[suggestion.parameter] = normalized
        self._save_runtime_overrides(overrides)
        self.current_params = self._load_current_params()
        logger.info(
            "Applied optimizer override: %s %r -> %r (%s)",
            suggestion.parameter,
            current_value,
            normalized,
            suggestion.reason,
        )
        return True

    def run_optimization_cycle(self) -> Dict:
        """Run a full optimization cycle"""
        print("[AI Optimizer] Starting analysis...")

        analysis = self.analyze()
        applied_suggestions = []

        print(f"\n[AI Optimizer] Analysis Complete:")
        print(f"  Signals Generated: {analysis.total_signals}")
        print(f"  Signals Blocked: {analysis.blocked_signals}")
        print(f"  Trades Executed: {analysis.executed_trades}")
        print(f"\n  Rejection Reasons:")
        for reason, count in analysis.rejection_reasons.items():
            if count > 0:
                print(f"    - {reason}: {count}")

        print(f"\n  Bot Performance:")
        for bot, perf in analysis.bot_performance.items():
            print(f"    - {bot}: avg conf {perf['avg_confidence']:.0f}%")

        if analysis.suggestions:
            print(f"\n  Suggestions ({len(analysis.suggestions)}):")
            for s in analysis.suggestions:
                print(f"    [{s.impact}] {s.parameter}: {s.current_value} → {s.suggested_value}")
                print(f"         Reason: {s.reason}")
                print(f"         Confidence: {s.confidence}%")

                if self.auto_apply and s.confidence >= self.min_confidence:
                    print(f"         AUTO-APPLYING...")
                    if self.apply_suggestion(s):
                        applied_suggestions.append({
                            "parameter": s.parameter,
                            "value": self.current_params.get(s.parameter),
                            "reason": s.reason,
                        })
        else:
            print("\n  No suggestions - system appears optimized")

        result = asdict(analysis)
        result["effective_parameters"] = self.current_params
        result["applied_suggestions"] = applied_suggestions
        return result


# Singleton instance
_optimizer = None

def get_optimizer(auto_apply: Optional[bool] = None) -> AIOptimizer:
    """Get or create the AI optimizer singleton"""
    global _optimizer
    if _optimizer is None:
        resolved_auto_apply = auto_apply
        if resolved_auto_apply is None:
            resolved_auto_apply = os.getenv("AI_OPTIMIZER_AUTO_APPLY", "false").strip().lower() in {
                "1", "true", "yes", "on"
            }
        _optimizer = AIOptimizer(auto_apply=resolved_auto_apply)
    elif auto_apply is not None:
        _optimizer.auto_apply = auto_apply
    return _optimizer


if __name__ == "__main__":
    # Test the optimizer
    optimizer = AIOptimizer()
    optimizer.run_optimization_cycle()
