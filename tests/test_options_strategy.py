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


def test_itm_contract_with_open_interest_but_no_volume_is_selected():
    # Common for in-the-money contracts: plenty of open interest, few trades today.
    c = _contract("call", 95.0, _exp(35), open_interest=500, volume=0)
    result = select_contract([c], spot_price=100.0, direction="bullish", today=TODAY, settings=_settings())
    assert result is not None and result["strike"] == 95.0


def test_bogus_iv_borrows_near_the_money_iv():
    # yfinance often reports ~0 IV for ITM strikes, which would push delta to ~1.
    contracts = [
        _contract("call", 95.0, _exp(35), iv=0.00001),
        _contract("call", 100.0, _exp(35), iv=0.30, open_interest=10, volume=0),  # illiquid, but a sane IV source
        _contract("call", 104.0, _exp(35), iv=0.30, open_interest=10, volume=0),
    ]
    result = select_contract(contracts, spot_price=100.0, direction="bullish", today=TODAY, settings=_settings())
    assert result is not None
    assert result["strike"] == 95.0
    assert result["iv_estimated"] is True
    assert result["implied_volatility"] == 0.30
    assert 0.55 <= result["delta"] <= 0.75


def test_bogus_iv_without_fallback_is_skipped():
    c = _contract("call", 95.0, _exp(35), iv=9.0)
    assert select_contract([c], spot_price=100.0, direction="bullish", today=TODAY, settings=_settings()) is None


class _FakeSource:
    def __init__(self, expirations, chain):
        self.expirations, self.chain = expirations, chain

    def get_option_expirations(self, symbol):
        return self.expirations

    def get_option_chain(self, symbol, expirations=None):
        return self.chain


def _reason(source, direction="bearish", spot=100.0):
    from app.scan import options_strategy
    return options_strategy.find_recommended_contract_with_reason(source, "U", spot, direction, _settings())


def _real_exp(days_out):
    return (date.today() + timedelta(days=days_out)).strftime("%Y-%m-%d")


def test_reason_no_expirations():
    assert _reason(_FakeSource(None, None)) == (None, "No option expirations listed")


def test_reason_no_expiration_in_window():
    assert _reason(_FakeSource([_real_exp(7), _real_exp(90)], None)) == (None, "No expiration 30-45 days out")


def test_reason_chain_unavailable():
    assert _reason(_FakeSource([_real_exp(35)], None)) == (None, "Options chain unavailable")


def test_reason_nothing_fits_delta_or_liquidity():
    chain = [_contract("put", 150.0, _real_exp(35))]  # deep ITM put, delta ~-1
    assert _reason(_FakeSource([_real_exp(35)], chain)) == (None, "No put near 0.65 delta with enough open interest")


def test_reason_no_puts_listed_in_window():
    chain = [_contract("call", 95.0, _real_exp(35))]
    assert _reason(_FakeSource([_real_exp(35)], chain)) == (None, "No puts listed 30-45 days out")


def test_found_contract_has_no_reason():
    chain = [_contract("put", 105.0, _real_exp(35), open_interest=300, volume=0)]
    contract, reason = _reason(_FakeSource([_real_exp(35)], chain))
    assert reason is None and contract["option_type"] == "put"


def test_prefers_expiration_before_earnings():
    # Earnings on day 38: the day-35 contract avoids it, the day-42 one spans it,
    # even though the day-42 one has more open interest.
    contracts = [
        _contract("call", 95.0, _exp(35), open_interest=100),
        _contract("call", 95.0, _exp(42), open_interest=5000),
    ]
    result = select_contract(
        contracts, spot_price=100.0, direction="bullish", today=TODAY, settings=_settings(),
        earnings_date=_exp(38),
    )
    assert result["expiration"] == _exp(35)
    assert result["spans_earnings"] is False


def test_falls_back_to_spanning_earnings_when_nothing_earlier_qualifies():
    contracts = [_contract("call", 95.0, _exp(42))]
    result = select_contract(
        contracts, spot_price=100.0, direction="bullish", today=TODAY, settings=_settings(),
        earnings_date=_exp(38),
    )
    assert result["expiration"] == _exp(42)
    assert result["spans_earnings"] is True


def test_earnings_outside_window_changes_nothing():
    contracts = [_contract("call", 95.0, _exp(35), open_interest=100), _contract("call", 95.0, _exp(42), open_interest=5000)]
    result = select_contract(
        contracts, spot_price=100.0, direction="bullish", today=TODAY, settings=_settings(),
        earnings_date=_exp(80),
    )
    assert result["expiration"] == _exp(42)  # normal liquidity preference
    assert result["spans_earnings"] is False


def test_nan_open_interest_and_volume_fail_liquidity_after_parsing():
    import pandas as pd
    from app.datasource.yfinance_source import _parse_chain_rows

    nan = float("nan")
    df = pd.DataFrame([
        {"strike": 95.0, "volume": nan, "openInterest": nan, "lastPrice": 5.0, "bid": nan, "ask": 5.1,
         "contractSymbol": "X1", "impliedVolatility": 0.30},
        {"strike": nan, "volume": 5, "openInterest": 5, "lastPrice": 1.0, "bid": 1, "ask": 1.1,
         "contractSymbol": "X2", "impliedVolatility": 0.30},
    ])
    parsed = _parse_chain_rows(df, _exp(35), "call")
    assert len(parsed) == 1  # missing strike skipped
    assert parsed[0]["volume"] == 0.0 and parsed[0]["open_interest"] == 0.0 and parsed[0]["bid"] is None
    assert select_contract(parsed, spot_price=100.0, direction="bullish", today=TODAY, settings=_settings()) is None


def test_nan_volume_no_longer_breaks_call_put_ratio():
    import pandas as pd
    from app.datasource.yfinance_source import _parse_chain_rows
    from app.scan.options_flow import compute_options_activity

    nan = float("nan")
    calls = pd.DataFrame([
        {"strike": 100.0, "volume": 300, "openInterest": 100, "lastPrice": 2.0, "contractSymbol": "C1", "impliedVolatility": 0.9},
        {"strike": 110.0, "volume": nan, "openInterest": 50, "lastPrice": 0.5, "contractSymbol": "C2", "impliedVolatility": 0.9},
    ])
    puts = pd.DataFrame([
        {"strike": 90.0, "volume": 100, "openInterest": 400, "lastPrice": 1.5, "contractSymbol": "P1", "impliedVolatility": 0.9},
    ])
    contracts = _parse_chain_rows(calls, _exp(10), "call") + _parse_chain_rows(puts, _exp(10), "put")
    result = compute_options_activity(contracts)
    assert result["call_put_ratio"] == 3.0
