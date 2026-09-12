"""Breakout scoring: blends gap %, range expansion (vs ATR14), and distance
beyond the prior day's high/low into a single 0-100 score.
"""

from __future__ import annotations

from app import db
from app.config import load_settings


def get_prior_day_ohlc(symbol: str) -> dict | None:
    """Most recent daily_ohlc row for `symbol` (yesterday's close/high/low/ATR)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM daily_ohlc WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def compute_breakout(
    current_price: float,
    today_high: float,
    today_low: float,
    prior: dict,
) -> dict:
    settings = load_settings()
    cfg = settings.get("scoring", "breakout", default={})
    gap_cap = cfg.get("gap_cap_pct", 5.0)
    range_cap = cfg.get("range_cap_atr_multiple", 1.5)
    level_cap = cfg.get("level_cap_pct", 2.0)
    weights = cfg.get("weights", {"gap": 0.40, "range": 0.35, "level": 0.25})

    prior_close = prior.get("close")
    prior_high = prior.get("high")
    prior_low = prior.get("low")
    atr14 = prior.get("atr14")

    gap_pct = None
    if prior_close:
        gap_pct = (current_price - prior_close) / prior_close * 100.0

    range_expansion = None
    if atr14 and atr14 > 0:
        range_expansion = (today_high - today_low) / atr14

    breakout_level_pct = None
    if prior_high and prior_low:
        above = (current_price - prior_high) / prior_high * 100.0
        below = (prior_low - current_price) / prior_low * 100.0
        breakout_level_pct = max(above, below, 0.0)

    components, weight_sum = [], 0.0
    if gap_pct is not None:
        components.append(weights.get("gap", 0.40) * min(abs(gap_pct) / gap_cap, 1.0) * 100.0)
        weight_sum += weights.get("gap", 0.40)
    if range_expansion is not None:
        components.append(weights.get("range", 0.35) * min(range_expansion / range_cap, 1.0) * 100.0)
        weight_sum += weights.get("range", 0.35)
    if breakout_level_pct is not None:
        components.append(weights.get("level", 0.25) * min(breakout_level_pct / level_cap, 1.0) * 100.0)
        weight_sum += weights.get("level", 0.25)

    breakout_score = sum(components) / weight_sum if weight_sum > 0 else None

    direction = None
    if prior_high and current_price > prior_high:
        direction = "bullish"
    elif prior_low and current_price < prior_low:
        direction = "bearish"
    elif gap_pct is not None:
        direction = "bullish" if gap_pct >= 0 else "bearish"

    return {
        "gap_pct": gap_pct,
        "range_expansion": range_expansion,
        "breakout_level_pct": breakout_level_pct,
        "breakout_score": breakout_score,
        "breakout_direction": direction,
    }
