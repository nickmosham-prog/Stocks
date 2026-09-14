"""Combines the component scores into a single ranked Alpha Score.

Missing components (e.g. no listed options) are excluded and the remaining
weights are renormalized, so an incomplete-data ticker isn't unfairly
dragged toward zero.
"""

from __future__ import annotations

from app.config import load_settings
from app.scan.numeric import clean, is_valid

# Fixed internal blend used only for the cheap first-pass ranking that
# decides which tickers get the expensive options/news enrichment each
# cycle. Not user-configurable - it is an implementation detail of the
# two-phase pipeline, not the final displayed Alpha Score.
PRELIM_RVOL_WEIGHT = 0.55
PRELIM_BREAKOUT_WEIGHT = 0.45


def _weighted_average(pairs: list[tuple[float | None, float]]) -> float | None:
    # is_valid (not a bare `is not None`) matters here: a stray NaN is
    # "not None" too, and NaN * weight poisons the whole sum silently.
    total_weight = sum(weight for score, weight in pairs if is_valid(score))
    if total_weight <= 0:
        return None
    return clean(sum(score * weight for score, weight in pairs if is_valid(score)) / total_weight)


def preliminary_score(rvol_score: float | None, breakout_score: float | None) -> float | None:
    return _weighted_average(
        [(rvol_score, PRELIM_RVOL_WEIGHT), (breakout_score, PRELIM_BREAKOUT_WEIGHT)]
    )


def compute_alpha_score(
    rvol_score: float | None, breakout_score: float | None, options_score: float | None
) -> float | None:
    weights = load_settings().get(
        "scoring", "alpha_weights", default={"rvol": 0.40, "breakout": 0.35, "options": 0.25}
    )
    return _weighted_average(
        [
            (rvol_score, weights.get("rvol", 0.40)),
            (breakout_score, weights.get("breakout", 0.35)),
            (options_score, weights.get("options", 0.25)),
        ]
    )
