from datetime import date, timedelta

from app.config import Settings
from app.scan.options_strategy import select_contract

TODAY = date(2026, 9, 1)


def _settings(**overrides):
    cfg = {
        "dte_min": 30,
        "dte_max": 45,
        "target_delta_call": 0.65,
        "target_delta_put": -0.65,
        "delta_band": 0.10,
        "risk_free_rate": 0.045,
        "min_open_interest": 50,
        "min_volume": 10,
        **overrides,
    }
    return Settings(raw={"scoring": {"options_strategy": cfg}})


def _exp(days_out):
    return (TODAY + timedelta(days=days_out)).strftime("%Y-%m-%d")


def _contract(option_type, strike, expiration, iv=0.30, open_interest=200, volume=100, last_price=5.0, ask=5.1):
    return {
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
        "implied_volatility": iv,
        "open_interest": open_interest,
        "volume": volume,
        "last_price": last_price,
        "ask": ask,
        "contract_symbol": f"TEST{option_type[0].upper()}{strike}",
    }


def test_selects_call_near_target_delta_within_dte_window():
    # Spot 100, IV 0.30, DTE 35 (verified via compute_greeks directly):
    # strike 85 -> delta ~0.97 (too far ITM), 95 -> ~0.74 (within 0.55-0.75
    # band around the 0.65 target), 110 -> ~0.18 (too far OTM).
    contracts = [
        _contract("call", 85.0, _exp(35)),
        _contract("call", 95.0, _exp(35)),
        _contract("call", 110.0, _exp(35)),
    ]
    result = select_contract(contracts, spot_price=100.0, direction="bullish", today=TODAY, settings=_settings())
    assert result is not None
    assert result["strike"] == 95.0
    assert 0.55 <= result["delta"] <= 0.75


def test_selects_put_for_bearish_direction():
    # strike 105 -> put delta ~-0.67 (within -0.75 to -0.55 band).
    contracts = [
        _contract("put", 105.0, _exp(35)),
        _contract("call", 95.0, _exp(35)),   # wrong type, must be excluded
    ]
    result = select_contract(contracts, spot_price=100.0, direction="bearish", today=TODAY, settings=_settings())
    assert result is not None
    assert result["option_type"] == "put"
    assert -0.75 <= result["delta"] <= -0.55


def test_excludes_expirations_outside_dte_window():
    contracts = [
        _contract("call", 92.0, _exp(5)),    # too soon
        _contract("call", 92.0, _exp(90)),   # too far
    ]
    result = select_contract(contracts, spot_price=100.0, direction="bullish", today=TODAY, settings=_settings())
    assert result is None


def test_excludes_illiquid_contracts():
    # strike 95 -> delta ~0.74, within the target band - isolates the
    # liquidity filter specifically (not incidentally excluded by delta).
    contracts = [_contract("call", 95.0, _exp(35), open_interest=5, volume=1)]
    result = select_contract(contracts, spot_price=100.0, direction="bullish", today=TODAY, settings=_settings())
    assert result is None


def test_liquidity_tie_break_prefers_higher_open_interest():
    # Both strikes land within the delta band (95 -> ~0.74, 96 -> ~0.70).
    contracts = [
        _contract("call", 95.0, _exp(35), open_interest=100, volume=50),
        _contract("call", 96.0, _exp(35), open_interest=500, volume=50),
    ]
    result = select_contract(contracts, spot_price=100.0, direction="bullish", today=TODAY, settings=_settings())
    assert result is not None
    assert result["strike"] == 96.0


def test_no_candidates_returns_none_not_raises():
    assert select_contract([], spot_price=100.0, direction="bullish", today=TODAY, settings=_settings()) is None


def test_missing_price_excludes_contract():
    c = _contract("call", 95.0, _exp(35), last_price=0.0, ask=None)
    result = select_contract([c], spot_price=100.0, direction="bullish", today=TODAY, settings=_settings())
    assert result is None
