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


def test_breakout_level_pct_below_prior_low():
    result = breakout.compute_breakout(current_price=90.0, today_high=95.0, today_low=90.0, prior=_prior())
    assert result["breakout_level_pct"] > 0
    assert result["breakout_direction"] == "bearish"


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
