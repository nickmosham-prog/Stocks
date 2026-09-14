from app.scan import breakout


def _prior(close=100.0, high=102.0, low=98.0, atr14=2.0):
    return {"close": close, "high": high, "low": low, "atr14": atr14}


def test_gap_pct_computed_from_prior_close():
    result = breakout.compute_breakout(current_price=105.0, today_high=106.0, today_low=104.0, prior=_prior())
    assert round(result["gap_pct"], 2) == 5.0


def test_breakout_level_pct_above_prior_high():
    # price 110 vs prior high 102 -> (110-102)/102*100 ~= 7.84%
    result = breakout.compute_breakout(current_price=110.0, today_high=110.0, today_low=105.0, prior=_prior())
    assert result["breakout_level_pct"] > 0
    assert result["breakout_direction"] == "bullish"
    assert result["level_broken"] is True


def test_breakout_level_pct_below_prior_low():
    result = breakout.compute_breakout(current_price=90.0, today_high=95.0, today_low=90.0, prior=_prior())
    assert result["breakout_level_pct"] > 0
    assert result["breakout_direction"] == "bearish"
    assert result["level_broken"] is True


def test_level_not_broken_when_price_inside_prior_range():
    # price is up on the day (gap) but still within yesterday's high/low
    result = breakout.compute_breakout(current_price=101.0, today_high=101.5, today_low=100.5, prior=_prior())
    assert result["breakout_direction"] == "bullish"  # directional bias from gap
    assert result["level_broken"] is False  # but no actual level break yet


def test_breakout_score_is_capped_between_0_and_100():
    # extreme gap should still cap the score at 100
    result = breakout.compute_breakout(current_price=500.0, today_high=500.0, today_low=100.0, prior=_prior())
    assert 0.0 <= result["breakout_score"] <= 100.0


def test_breakout_handles_missing_atr():
    prior = _prior(atr14=None)
    result = breakout.compute_breakout(current_price=101.0, today_high=102.0, today_low=100.0, prior=prior)
    assert result["range_expansion"] is None
    # score still computable from gap + level components
    assert result["breakout_score"] is not None


def test_nan_today_high_low_does_not_poison_breakout_score():
    # Regression: an all-NaN High/Low session (seen on thin/illiquid names)
    # used to leave range_expansion as NaN rather than None, and
    # `if range_expansion is not None:` let it through since NaN is not
    # None - poisoning the whole breakout_score (and, downstream, the
    # Alpha Score and email alerts) into NaN instead of a real number.
    nan = float("nan")
    result = breakout.compute_breakout(current_price=101.0, today_high=nan, today_low=nan, prior=_prior())
    assert result["range_expansion"] is None
    assert result["breakout_score"] is not None
    import math
    assert not math.isnan(result["breakout_score"])
