"""Calculations module — base metrics, advanced metrics, extrapolation, and scoring."""

from .base_metrics import compute_base_metrics, filter_window
from .advanced_metrics import (
    compute_advanced_metrics,
    compute_side_bias,
    compute_pcr_bias,
    compute_slope_acceleration,
)
from .extrapolation import extrapolate_otm_strikes
from .scoring import score_and_select, update_rows_with_scores, ScoredCandidate

__all__ = [
    "compute_base_metrics",
    "filter_window",
    "compute_advanced_metrics",
    "compute_side_bias",
    "compute_pcr_bias",
    "compute_slope_acceleration",
    "extrapolate_otm_strikes",
    "score_and_select",
    "update_rows_with_scores",
    "ScoredCandidate",
]
