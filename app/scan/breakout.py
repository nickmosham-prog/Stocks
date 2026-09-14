"""Breakout scoring: blends gap %, range expansion (vs ATR14), and distance
beyond the prior day's high/low into a single 0-100 score. Also tracks
whether a level break is *holding* across consecutive scan cycles, since a
single instantaneous trigger is a weaker signal than a level that's stayed
broken for several minutes.
"""

from __future__ import annotations

from datetime import datetime

from app import db
from app.config import load_settings
from app.scan.numeric import clean


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
        gap_pct = clean((current_price - prior_close) / prior_close * 100.0)

    range_expansion = None
    if atr14 and atr14 > 0:
        range_expansion = clean((today_high - today_low) / atr14)

    breakout_level_pct = None
    if prior_high and prior_low:
        above = (current_price - prior_high) / prior_high * 100.0
        below = (prior_low - current_price) / prior_low * 100.0
        breakout_level_pct = clean(max(above, below, 0.0))

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

    breakout_score = clean(sum(components) / weight_sum) if weight_sum > 0 else None

    level_broken = bool((prior_high and current_price > prior_high) or (prior_low and current_price < prior_low))

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
        "level_broken": level_broken,
    }


def update_hold_state(
    level_broken: bool,
    direction: str | None,
    now: datetime,
    prev_holding_since: str | None,
    prev_direction: str | None,
) -> dict:
    """Tracks how long a level break has held across consecutive scan cycles.

    `prev_holding_since`/`prev_direction` come from the ticker's previous
    latest_snapshot row (None if this is the first time we've scored it, or
    it wasn't broken last cycle). Same direction as last cycle -> keep
    accumulating; direction changed, or the level is no longer broken ->
    reset. Returns {holding_since, hold_minutes, confirmed} where
    `confirmed` is computed by the caller against the configured threshold
    (kept out of this function so it stays a pure "how long has this held"
    calculation, easy to test independent of settings).
    """
    if not level_broken:
        return {"holding_since": None, "hold_minutes": None}

    if prev_holding_since and prev_direction == direction:
        holding_since = datetime.fromisoformat(prev_holding_since)
    else:
        holding_since = now

    hold_minutes = (now - holding_since).total_seconds() / 60.0
    return {"holding_since": holding_since.isoformat(), "hold_minutes": hold_minutes}
