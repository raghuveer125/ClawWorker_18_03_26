from __future__ import annotations

from typing import Dict, List

from contracts import ShadowComparisonResult, ShadowRow


class ShadowComparator:
    @staticmethod
    def _winner(row: ShadowRow) -> str:
        baseline_correct = row.baseline_action == row.realized_outcome_action
        institutional_correct = row.institutional_action == row.realized_outcome_action

        if institutional_correct and not baseline_correct:
            return "institutional"
        if baseline_correct and not institutional_correct:
            return "baseline"
        return "tie"

    def compare(self, rows: List[ShadowRow]) -> ShadowComparisonResult:
        total = len(rows)
        agreement = sum(1 for row in rows if row.baseline_action == row.institutional_action)
        disagreement = total - agreement

        institutional_better = 0
        baseline_better = 0
        for row in rows:
            winner = self._winner(row)
            if winner == "institutional":
                institutional_better += 1
            elif winner == "baseline":
                baseline_better += 1

        return ShadowComparisonResult(
            total_rows=total,
            agreement_count=agreement,
            disagreement_count=disagreement,
            agreement_pct=round((100.0 * agreement / total), 2) if total else 0.0,
            institutional_better_count=institutional_better,
            baseline_better_count=baseline_better,
        )

    @staticmethod
    def to_dict(result: ShadowComparisonResult) -> Dict[str, object]:
        return {
            "total_rows": result.total_rows,
            "agreement_count": result.agreement_count,
            "disagreement_count": result.disagreement_count,
            "agreement_pct": result.agreement_pct,
            "institutional_better_count": result.institutional_better_count,
            "baseline_better_count": result.baseline_better_count,
        }
