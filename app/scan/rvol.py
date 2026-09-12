"""Relative volume (RVOL): today's cumulative volume-so-far vs the historical
cumulative average for the same point in the session.
"""

from __future__ import annotations

from app import db
from app.config import load_settings


def cumulative_avg_volume(symbol: str, session: str, bucket_index: int) -> float | None:
    """Sum of historical avg_volume for buckets [0, bucket_index] of this session."""
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT SUM(avg_volume) AS total, COUNT(*) AS n
            FROM historical_volume_profile
            WHERE symbol = ? AND session = ? AND bucket_index <= ?
            """,
            (symbol, session, bucket_index),
        )
        row = cur.fetchone()
    if row is None or row["total"] is None or row["n"] == 0:
        return None
    return float(row["total"])


def compute_rvol(cum_volume_today: float, cum_avg_volume: float | None) -> float | None:
    if cum_avg_volume is None or cum_avg_volume <= 0:
        return None
    return cum_volume_today / cum_avg_volume


def compute_rvol_score(rvol: float | None) -> float | None:
    if rvol is None:
        return None
    cap = load_settings().get("scoring", "rvol", "cap", default=5.0)
    return max(0.0, min(rvol / cap, 1.0)) * 100.0
