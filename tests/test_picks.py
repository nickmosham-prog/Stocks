from datetime import date

from app.config import Settings
from app.scan import picks


def _settings(**picks_overrides):
    return Settings(
        raw={
            "scoring": {"fundamentals": {"max_pe": 60}},
            "picks": {"max_extension_above_sma50_pct": 15, **picks_overrides},
        }
    )


def _row(**overrides):
    row = {
        "symbol": "GOOD",
        "price": 105.0,
        "gap_pct": 3.0,
        "rvol": 2.5,
        "breakout_direction": "bullish",
        "breakout_level_pct": 1.0,
        "breakout_confirmed": 1,
        "breakout_hold_minutes": 25.0,
        "call_put_ratio": 3.0,
        "max_vol_oi_ratio": 1.5,
        "fundamentals_status": "pass",
    }
    row.update(overrides)
    return row


def _fund(**overrides):
    fund = {
        "fundamentals_status": "pass",
        "trailing_pe": 22.0,
        "revenue_growth_yoy": 0.20,
        "profit_margin": 0.22,
        "target_mean_price": 130.0,
        "recommendation_mean": 1.8,
        "analyst_count": 25,
        "next_earnings_date": None,
    }
    fund.update(overrides)
    return fund


def _trend(**overrides):
    trend = {
        "sma50": 100.0,
        "sma200": 90.0,
        "high_52w": 108.0,
        "pct_from_high": -2.8,
        "rs_3m_pct": 12.0,
    }
    trend.update(overrides)
    return trend


# --- eligibility ------------------------------------------------------------

def test_strong_bullish_quality_stock_is_eligible_and_scores_high():
    result = picks.score_row(_row(), _fund(), _trend(), _settings())
    assert result["eligible"] is True
    assert result["pick_score"] > 70


def test_falling_stock_is_never_eligible():
    # The NFLX case: huge volume, but down on the day.
    result = picks.score_row(_row(gap_pct=-5.9, breakout_direction="bearish"), _fund(), _trend(), _settings())
    assert result["eligible"] is False


def test_below_50_day_average_is_not_eligible():
    assert picks.is_eligible(_row(price=95.0), _trend(sma50=100.0)) is False


def test_failing_or_unknown_fundamentals_not_eligible():
    assert picks.is_eligible(_row(fundamentals_status="fail"), _trend()) is False
    assert picks.is_eligible(_row(fundamentals_status="unknown"), _trend()) is False


def test_missing_trend_data_not_eligible():
    assert picks.is_eligible(_row(), None) is False


# --- components -------------------------------------------------------------

def test_momentum_only_rewards_upside():
    assert picks.momentum_score(_row(gap_pct=-4.0, rvol=3.0, breakout_direction="bearish")) == 0
    assert picks.momentum_score(_row(gap_pct=4.0, rvol=3.0)) == 100


def test_trend_score_rewards_uptrend_over_downtrend():
    up = picks.trend_score(105.0, _trend())
    down = picks.trend_score(80.0, _trend(sma50=100.0, sma200=110.0, pct_from_high=-35.0, rs_3m_pct=-10.0))
    assert up > 80
    assert down == 0


def test_missing_components_are_renormalized_not_zeroed():
    # No options data and no analyst coverage: the remaining components
    # carry the score instead of dragging it toward zero.
    full = picks.score_row(_row(), _fund(), _trend(), _settings())["pick_score"]
    partial = picks.score_row(
        _row(call_put_ratio=None, max_vol_oi_ratio=None),
        _fund(target_mean_price=None, recommendation_mean=None, analyst_count=None),
        _trend(),
        _settings(),
    )
    assert partial["options_score"] is None
    assert partial["analyst_score"] is None
    assert partial["pick_score"] > 60
    assert abs(partial["pick_score"] - full) < 20


def test_analyst_score_ignores_thin_coverage():
    assert picks.analyst_score(100.0, _fund(analyst_count=2)) is None


def test_bearish_options_skew_scores_zero_skew():
    score = picks.options_score(_row(call_put_ratio=0.5, max_vol_oi_ratio=0.0))
    assert score == 0


def test_quality_score_prefers_growth_and_margin():
    strong = picks.quality_score(_fund(), _settings())
    weak = picks.quality_score(_fund(revenue_growth_yoy=0.01, profit_margin=0.02, trailing_pe=58.0), _settings())
    assert strong > weak


def test_nan_inputs_do_not_poison_score():
    nan = float("nan")
    result = picks.score_row(_row(rvol=nan, call_put_ratio=nan), _fund(profit_margin=nan), _trend(rs_3m_pct=nan), _settings())
    assert result["pick_score"] == result["pick_score"]  # not NaN


# --- ranking ----------------------------------------------------------------

def test_rank_picks_applies_floor_eligibility_and_count():
    candidates = [
        {"symbol": "A", "eligible": True, "pick_score": 80},
        {"symbol": "B", "eligible": True, "pick_score": 50},   # below floor
        {"symbol": "C", "eligible": False, "pick_score": 95},  # ineligible
        {"symbol": "D", "eligible": True, "pick_score": 70},
        {"symbol": "E", "eligible": True, "pick_score": None},
        {"symbol": "F", "eligible": True, "pick_score": 60},
    ]
    ranked = picks.rank_picks(candidates, count=2, min_score=55)
    assert [c["symbol"] for c in ranked] == ["A", "D"]


def test_rank_picks_can_return_nothing():
    assert picks.rank_picks([{"symbol": "A", "eligible": True, "pick_score": 40}], min_score=55) == []


# --- thesis / risks ---------------------------------------------------------

def test_thesis_reasons_cite_the_numbers():
    reasons, risks = picks.build_thesis(_row(), _fund(), _trend(), today=date(2026, 9, 23), settings=_settings())
    text = "\n".join(reasons)
    assert "Up 3.0% today on 2.5x normal volume" in text
    assert "holding above yesterday's high for 25 min" in text
    assert "Above its 50-day and 200-day averages" in text
    assert "beating the S&P 500 by 12 pts" in text
    assert "Revenue +20% YoY" in text
    assert "P/E 22" in text
    assert "Analysts' average target $130.00 (+24% from here, 25 analysts, consensus Buy)" in text
    assert "call volume 3.0x put volume" in text
    assert risks == []


def test_earnings_before_expiration_is_flagged():
    contract = {"expiration": "2026-10-30", "strike": 100.0, "price": 8.0, "open_interest": 5000}
    _, risks = picks.build_thesis(
        _row(), _fund(next_earnings_date="2026-10-20"), _trend(), contract, today=date(2026, 9, 23), settings=_settings()
    )
    assert any("Earnings on 2026-10-20" in r for r in risks)


def test_earnings_after_expiration_not_flagged():
    contract = {"expiration": "2026-10-30", "strike": 100.0, "price": 8.0, "open_interest": 5000}
    _, risks = picks.build_thesis(
        _row(), _fund(next_earnings_date="2026-11-15"), _trend(), contract, today=date(2026, 9, 23), settings=_settings()
    )
    assert not any("Earnings" in r for r in risks)


def test_extended_and_thin_liquidity_risks():
    contract = {"expiration": "2026-10-30", "strike": 120.0, "price": 8.0, "open_interest": 80}
    _, risks = picks.build_thesis(
        _row(price=125.0), _fund(), _trend(sma50=100.0), contract, today=date(2026, 9, 23), settings=_settings()
    )
    assert any("Extended: 25% above its 50-day average" in r for r in risks)
    assert any("Thin options liquidity" in r for r in risks)


def test_breakeven_for_long_call():
    level, move = picks.breakeven({"strike": 100.0, "price": 6.0}, 102.0)
    assert level == 106.0
    assert round(move, 2) == round((106 / 102 - 1) * 100, 2)
    assert picks.breakeven(None, 100.0) == (None, None)
