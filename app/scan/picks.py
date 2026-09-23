"""Top Picks: a ranked, reasoned list of bullish swing-trade ideas.

Unlike the Alpha Score (which measures *how unusual* today is, in either
direction), the Pick Score measures *how good a bullish swing setup* a
stock is, blending five components:

- trend     - above its 50/200-day averages, near its 52-week high,
              outperforming the S&P 500 over 3 months
- momentum  - up today (upside only - a drop never scores), on above-normal
              volume, breaking/holding above yesterday's high
- quality   - revenue growth, profit margin, reasonable P/E
- analyst   - upside to Wall Street's average price target, rating
- options   - options traders leaning bullish (call volume vs put volume)

A missing component (e.g. no analyst coverage, no options data this cycle)
is dropped and the remaining weights renormalized, same as the Alpha Score.

Every pick comes with plain-English reasons and risks built from the actual
numbers, so the user sees *why*, not just a score. This is a screening
tool - the reasons describe what the data shows, not a guarantee.
"""

from __future__ import annotations

import math
from datetime import date, datetime

from app.config import load_settings
from app.scan.alpha_score import _weighted_average
from app.scan.numeric import clean, is_valid

DEFAULT_WEIGHTS = {"trend": 0.30, "momentum": 0.25, "quality": 0.25, "analyst": 0.10, "options": 0.10}
COMPONENTS = ("trend", "momentum", "quality", "analyst", "options")


def _clamp01(x: float) -> float:
    return max(0.0, min(x, 1.0))


def _partial_score(parts: list[tuple[float | None, float]]) -> float | None:
    """parts: (fraction_0_to_1 or None, max_points). Score = earned points /
    points available among the parts that could be computed, x100."""
    available = [(f, pts) for f, pts in parts if f is not None]
    total = sum(pts for _, pts in available)
    if total <= 0:
        return None
    return clean(sum(f * pts for f, pts in available) / total * 100.0)


def trend_score(price: float | None, trend: dict | None) -> float | None:
    trend = trend or {}
    sma50, sma200 = trend.get("sma50"), trend.get("sma200")
    pct_from_high, rs = trend.get("pct_from_high"), trend.get("rs_3m_pct")
    price_ok = is_valid(price)
    return _partial_score([
        (float(price > sma50) if price_ok and is_valid(sma50) else None, 25),
        (float(price > sma200) if price_ok and is_valid(sma200) else None, 20),
        (float(sma50 > sma200) if is_valid(sma50) and is_valid(sma200) else None, 15),
        (_clamp01(1 - abs(pct_from_high) / 25.0) if is_valid(pct_from_high) else None, 20),
        (_clamp01(rs / 20.0) if is_valid(rs) else None, 20),
    ])


def momentum_score(row: dict) -> float | None:
    change, rvol = row.get("gap_pct"), row.get("rvol")
    bullish = row.get("breakout_direction") == "bullish"
    broke_high = bullish and is_valid(row.get("breakout_level_pct")) and row["breakout_level_pct"] > 0
    if not is_valid(change):
        return None
    # Heavy volume only counts when the stock is rising - heavy volume on
    # a down day is selling pressure, not bullish momentum.
    rising = change > 0
    return _partial_score([
        (_clamp01(change / 4.0), 40),
        ((_clamp01((rvol - 1.0) / 2.0) if rising else 0.0) if is_valid(rvol) else None, 30),
        (float(broke_high), 15),
        (float(broke_high and bool(row.get("breakout_confirmed"))), 15),
    ])


def quality_score(fund: dict | None, settings=None) -> float | None:
    fund = fund or {}
    settings = settings or load_settings()
    max_pe = settings.get("scoring", "fundamentals", "max_pe", default=60)
    growth, margin, pe = fund.get("revenue_growth_yoy"), fund.get("profit_margin"), fund.get("trailing_pe")
    pe_span = max(max_pe - 15.0, 1.0)
    return _partial_score([
        (_clamp01(growth / 0.30) if is_valid(growth) else None, 40),
        (_clamp01(margin / 0.25) if is_valid(margin) else None, 30),
        (_clamp01(1 - (pe - 15.0) / pe_span) if is_valid(pe) and pe > 0 else None, 30),
    ])


def analyst_upside_pct(price: float | None, fund: dict | None) -> float | None:
    target = (fund or {}).get("target_mean_price")
    if not (is_valid(price) and price > 0 and is_valid(target) and target > 0):
        return None
    return clean((target / price - 1.0) * 100.0)


def analyst_score(price: float | None, fund: dict | None) -> float | None:
    fund = fund or {}
    count = fund.get("analyst_count")
    if is_valid(count) and count < 3:
        return None  # one or two analysts isn't a consensus
    upside = analyst_upside_pct(price, fund)
    rec = fund.get("recommendation_mean")
    return _partial_score([
        (_clamp01(upside / 40.0) if upside is not None else None, 70),
        (_clamp01((3.0 - rec) / 2.0) if is_valid(rec) else None, 30),
    ])


def options_score(row: dict) -> float | None:
    cpr, vol_oi = row.get("call_put_ratio"), row.get("max_vol_oi_ratio")
    if not is_valid(cpr) or cpr <= 0:
        return None
    return _partial_score([
        (_clamp01(math.log(cpr) / math.log(3.0)), 60),
        (_clamp01(vol_oi / 3.0) if is_valid(vol_oi) else None, 40),
    ])


def is_eligible(row: dict, trend: dict | None) -> bool:
    """Hard filters before scoring counts: real financials pass the quality
    gate (also excludes ETFs, which have no earnings), the stock is in an
    uptrend (above its 50-day average), and it isn't falling today."""
    if row.get("fundamentals_status") != "pass":
        return False
    price, change = row.get("price"), row.get("gap_pct")
    sma50 = (trend or {}).get("sma50")
    if not (is_valid(price) and is_valid(sma50) and price > sma50):
        return False
    return is_valid(change) and change >= 0


def score_row(row: dict, fund: dict | None, trend: dict | None, settings=None) -> dict:
    """Returns {pick_score, eligible, trend_score, momentum_score,
    quality_score, analyst_score, options_score}."""
    settings = settings or load_settings()
    weights = {**DEFAULT_WEIGHTS, **(settings.get("picks", "weights", default={}) or {})}
    components = {
        "trend": trend_score(row.get("price"), trend),
        "momentum": momentum_score(row),
        "quality": quality_score(fund, settings),
        "analyst": analyst_score(row.get("price"), fund),
        "options": options_score(row),
    }
    pick = _weighted_average([(components[name], weights.get(name, 0.0)) for name in COMPONENTS])
    return {
        "pick_score": pick,
        "eligible": is_eligible(row, trend),
        **{f"{name}_score": components[name] for name in COMPONENTS},
    }


def rank_picks(candidates: list[dict], count: int = 5, min_score: float = 55) -> list[dict]:
    """candidates: dicts with at least `eligible` and `pick_score`. Returns
    the top `count` eligible ones clearing `min_score`, best first - fewer
    (possibly none) on a weak day rather than padding with weak ideas."""
    qualifying = [
        c for c in candidates
        if c.get("eligible") and is_valid(c.get("pick_score")) and c["pick_score"] >= min_score
    ]
    qualifying.sort(key=lambda c: c["pick_score"], reverse=True)
    return qualifying[:count]


def _rating_label(rec: float) -> str:
    if rec <= 1.5:
        return "Strong Buy"
    if rec <= 2.5:
        return "Buy"
    if rec <= 3.5:
        return "Hold"
    return "Sell"


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def breakeven(contract: dict | None, spot: float | None) -> tuple[float | None, float | None]:
    """(breakeven price at expiration, % move from spot needed to reach it)
    for a long call: strike + premium paid."""
    if not contract or not is_valid(contract.get("strike")) or not is_valid(contract.get("price")):
        return None, None
    level = contract["strike"] + contract["price"]
    move = clean((level / spot - 1.0) * 100.0) if is_valid(spot) and spot > 0 else None
    return clean(level), move


def build_thesis(
    row: dict,
    fund: dict | None,
    trend: dict | None,
    contract: dict | None = None,
    today: date | None = None,
    settings=None,
) -> tuple[list[str], list[str]]:
    """Plain-English (reasons, risks) from the actual numbers."""
    settings = settings or load_settings()
    fund, trend = fund or {}, trend or {}
    today = today or date.today()
    price = row.get("price")
    reasons: list[str] = []
    risks: list[str] = []

    change, rvol = row.get("gap_pct"), row.get("rvol")
    if is_valid(change):
        text = f"Up {change:.1f}% today"
        if is_valid(rvol):
            text += f" on {rvol:.1f}x normal volume"
        if row.get("breakout_direction") == "bullish" and is_valid(row.get("breakout_level_pct")) \
                and row["breakout_level_pct"] > 0:
            minutes = row.get("breakout_hold_minutes")
            if row.get("breakout_confirmed") and is_valid(minutes):
                text += f", holding above yesterday's high for {minutes:.0f} min"
            else:
                text += ", breaking above yesterday's high"
        reasons.append(text)

    sma50, sma200 = trend.get("sma50"), trend.get("sma200")
    trend_bits = []
    if is_valid(price) and is_valid(sma50) and price > sma50:
        trend_bits.append(
            "Above its 50-day and 200-day averages"
            if is_valid(sma200) and price > sma200 else "Above its 50-day average"
        )
    pct_from_high = trend.get("pct_from_high")
    if is_valid(pct_from_high):
        trend_bits.append(
            "at a 52-week high" if pct_from_high > -1 else f"{abs(pct_from_high):.0f}% below its 52-week high"
        )
    rs = trend.get("rs_3m_pct")
    if is_valid(rs):
        trend_bits.append(
            f"{'beating' if rs >= 0 else 'lagging'} the S&P 500 by {abs(rs):.0f} pts over 3 months"
        )
    if trend_bits:
        reasons.append("; ".join(trend_bits))

    growth, margin, pe = fund.get("revenue_growth_yoy"), fund.get("profit_margin"), fund.get("trailing_pe")
    quality_bits = []
    if is_valid(growth):
        quality_bits.append(f"Revenue {growth * 100:+.0f}% YoY")
    if is_valid(margin):
        quality_bits.append(f"{margin * 100:.0f}% profit margin")
    if is_valid(pe):
        quality_bits.append(f"P/E {pe:.0f}")
    if quality_bits:
        reasons.append(", ".join(quality_bits) + " (profitable, passes the fundamentals check)")

    upside = analyst_upside_pct(price, fund)
    if upside is not None and upside > 0:
        text = f"Analysts' average target ${fund['target_mean_price']:.2f} ({upside:+.0f}% from here"
        count, rec = fund.get("analyst_count"), fund.get("recommendation_mean")
        if is_valid(count):
            text += f", {count:.0f} analysts"
        if is_valid(rec):
            text += f", consensus {_rating_label(rec)}"
        reasons.append(text + ")")

    cpr = row.get("call_put_ratio")
    if is_valid(cpr) and cpr >= 1.5:
        reasons.append(f"Options traders leaning bullish: call volume {cpr:.1f}x put volume")

    # --- risks ---
    earnings = _parse_date(fund.get("next_earnings_date"))
    horizon_end = _parse_date(contract.get("expiration")) if contract else None
    horizon_end = horizon_end or date.fromordinal(today.toordinal() + 45)
    if earnings and today <= earnings <= horizon_end:
        risks.append(
            f"Earnings on {earnings.isoformat()}, before the option expires - options often "
            "lose value right after earnings even when the stock moves your way (IV crush)"
        )
    max_ext = settings.get("picks", "max_extension_above_sma50_pct", default=15)
    if is_valid(price) and is_valid(sma50) and sma50 > 0:
        extension = (price / sma50 - 1.0) * 100.0
        if extension > max_ext:
            risks.append(f"Extended: {extension:.0f}% above its 50-day average - pullbacks are common from here")
    if is_valid(price) and is_valid(sma200) and price < sma200:
        risks.append("Still below its 200-day average (longer-term trend not yet repaired)")
    if upside is not None and upside < 0:
        risks.append(f"Already {abs(upside):.0f}% above analysts' average target")
    if contract and is_valid(contract.get("open_interest")) and contract["open_interest"] < 200:
        risks.append(
            f"Thin options liquidity (open interest {contract['open_interest']:.0f}) - use a limit order"
        )
    return reasons, risks
