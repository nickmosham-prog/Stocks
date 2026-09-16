from app.scan.options_greeks import compute_greeks


def test_atm_call_delta_is_roughly_half():
    # At-the-money, moderate IV/DTE -> delta should be close to 0.5
    # (slightly above due to the drift term, which is expected/correct).
    result = compute_greeks(spot_price=100.0, strike=100.0, days_to_expiration=35, implied_volatility=0.30, option_type="call")
    assert 0.45 < result["delta"] < 0.65


def test_deep_itm_call_delta_approaches_one():
    result = compute_greeks(spot_price=200.0, strike=50.0, days_to_expiration=35, implied_volatility=0.30, option_type="call")
    assert result["delta"] > 0.95


def test_deep_otm_call_delta_approaches_zero():
    result = compute_greeks(spot_price=50.0, strike=200.0, days_to_expiration=35, implied_volatility=0.30, option_type="call")
    assert result["delta"] < 0.05


def test_put_delta_equals_call_delta_minus_one():
    call = compute_greeks(spot_price=100.0, strike=95.0, days_to_expiration=35, implied_volatility=0.25, option_type="call")
    put = compute_greeks(spot_price=100.0, strike=95.0, days_to_expiration=35, implied_volatility=0.25, option_type="put")
    assert round(put["delta"] - (call["delta"] - 1.0), 6) == 0


def test_theta_is_negative_for_long_options():
    call = compute_greeks(spot_price=100.0, strike=100.0, days_to_expiration=35, implied_volatility=0.30, option_type="call")
    put = compute_greeks(spot_price=100.0, strike=100.0, days_to_expiration=35, implied_volatility=0.30, option_type="put")
    assert call["theta"] < 0
    assert put["theta"] < 0


def test_shorter_dated_atm_option_decays_faster():
    # Theta magnitude should be larger (more negative) for a near-dated
    # option than a longer-dated one, all else equal - the core reason
    # this app avoids weeklies for the recommended contract.
    near = compute_greeks(spot_price=100.0, strike=100.0, days_to_expiration=7, implied_volatility=0.30, option_type="call")
    far = compute_greeks(spot_price=100.0, strike=100.0, days_to_expiration=60, implied_volatility=0.30, option_type="call")
    assert abs(near["theta"]) > abs(far["theta"])


def test_none_on_zero_implied_volatility():
    assert compute_greeks(100.0, 100.0, 30, 0.0, "call") is None


def test_none_on_nan_implied_volatility():
    assert compute_greeks(100.0, 100.0, 30, float("nan"), "call") is None


def test_none_on_non_positive_days_to_expiration():
    assert compute_greeks(100.0, 100.0, 0, 0.3, "call") is None
    assert compute_greeks(100.0, 100.0, -5, 0.3, "call") is None


def test_none_on_non_positive_spot_or_strike():
    assert compute_greeks(0.0, 100.0, 30, 0.3, "call") is None
    assert compute_greeks(100.0, 0.0, 30, 0.3, "call") is None


def test_none_on_invalid_option_type():
    assert compute_greeks(100.0, 100.0, 30, 0.3, "straddle") is None
