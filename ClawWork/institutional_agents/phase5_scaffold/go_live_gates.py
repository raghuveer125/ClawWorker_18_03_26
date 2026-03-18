from __future__ import annotations

from typing import Dict, List

from contracts import GoLiveGateResult


class GoLiveGateEvaluator:
    def evaluate(
        self,
        performance_threshold_met: bool,
        risk_threshold_met: bool,
        monitoring_active: bool,
        rollback_tested: bool,
        shadow_mode_min_days_met: bool,
    ) -> GoLiveGateResult:
        checks = {
            "performance_threshold_met": bool(performance_threshold_met),
            "risk_threshold_met": bool(risk_threshold_met),
            "monitoring_active": bool(monitoring_active),
            "rollback_tested": bool(rollback_tested),
            "shadow_mode_min_days_met": bool(shadow_mode_min_days_met),
        }

        reasons: List[str] = [name for name, passed in checks.items() if not passed]
        passed = all(checks.values())

        return GoLiveGateResult(passed=passed, checks=checks, reasons=reasons)
