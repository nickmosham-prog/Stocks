"""Market backdrop: is the overall market healthy enough for bullish ideas?

Judged from the S&P 500 ETF (SPY): its trend (50- and 200-day averages)
and how it is doing today. Call options on individual stocks work worse
when the whole market is falling, so a weak backdrop raises the bar for
Top Picks and a cautious one adds a warning.
"""

from __future__ import annotations

from app.config import load_settings
from app.scan.numeric import is_valid

BENCHMARK = "SPY"
SECONDARY = "QQQ"
DEFAULTS = {"enabled": True, "caution_day_drop_pct": 1.0, "weak_min_score_bonus": 10, "weak_max_picks": 3}


def _config(settings) -> dict:
    return {**DEFAULTS, **(settings.get("picks", "market_regime", default={}) or {})}


def assess(
    spy_row: dict | None,
    spy_trend: dict | None,
    qqq_row: dict | None = None,
    settings=None,
) -> dict:
    """Returns {regime, note}; regime is 'healthy', 'caution', 'weak' or
    'unknown' (SPY not tracked / no data yet - no effect on picks)."""
    settings = settings or load_settings()
    cfg = _config(settings)
    spy_row, spy_trend = spy_row or {}, spy_trend or {}
    price, change = spy_row.get("price"), spy_row.get("gap_pct")
    sma50, sma200 = spy_trend.get("sma50"), spy_trend.get("sma200")
    if not cfg["enabled"] or not is_valid(price) or not is_valid(sma50):
        return {"regime": "unknown", "note": ""}

    below_50 = price < sma50
    below_200 = is_valid(sma200) and price < sma200
    falling = is_valid(change) and change <= -abs(cfg["caution_day_drop_pct"])

    if below_200 or (below_50 and falling):
        regime = "weak"
    elif below_50 or falling:
        regime = "caution"
    else:
        regime = "healthy"

    if below_200:
        trend_text = "below its 200-day average"
    elif below_50:
        trend_text = "below its 50-day average"
    elif is_valid(sma200):
        trend_text = "above its 50- and 200-day averages"
    else:
        trend_text = "above its 50-day average"
    today_text = f"{'up' if change >= 0 else 'down'} {abs(change):.1f}% today" if is_valid(change) else ""
    note = f"S&P 500 is {trend_text}" + (f" and {today_text}" if today_text else "")
    qqq_change = (qqq_row or {}).get("gap_pct")
    if is_valid(qqq_change):
        note += f"; Nasdaq 100 {qqq_change:+.1f}%"

    prefix = {
        "healthy": "Market backdrop is healthy: ",
        "caution": "Market caution: ",
        "weak": "Weak market - fewer picks and a higher bar: ",
    }[regime]
    return {"regime": regime, "note": prefix + note + "."}


def adjust_limits(regime: str, count: int, min_score: float, settings=None) -> tuple[int, float]:
    """In a weak market, fewer picks and a higher score floor."""
    if regime != "weak":
        return count, min_score
    cfg = _config(settings or load_settings())
    return min(count, cfg["weak_max_picks"]), min_score + cfg["weak_min_score_bonus"]
