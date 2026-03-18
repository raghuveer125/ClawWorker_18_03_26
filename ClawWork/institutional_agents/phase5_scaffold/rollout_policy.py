from __future__ import annotations

from typing import Dict, List

from contracts import RolloutStage


class RolloutPlanner:
    def __init__(self):
        self.stages = [
            RolloutStage(name="stage_5pct", allocation_pct=5, pass_required=True),
            RolloutStage(name="stage_25pct", allocation_pct=25, pass_required=True),
            RolloutStage(name="stage_50pct", allocation_pct=50, pass_required=True),
            RolloutStage(name="stage_100pct", allocation_pct=100, pass_required=True),
        ]

    def evaluate(self, gate_passed: bool) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        blocked = not gate_passed

        for stage in self.stages:
            rows.append(
                {
                    "stage": stage.name,
                    "allocation_pct": stage.allocation_pct,
                    "allowed": (not blocked),
                    "requires_pass": stage.pass_required,
                }
            )
            if stage.pass_required and blocked:
                blocked = True

        return rows
