from app.scan import options_flow


def _contract(option_type, volume, open_interest, iv):
    return {
        "expiration": "2099-01-01",
        "option_type": option_type,
        "strike": 100.0,
        "volume": volume,
        "open_interest": open_interest,
        "last_price": 1.0,
        "contract_symbol": f"TEST{option_type.upper()}",
        "implied_volatility": iv,
    }


def test_no_contracts_returns_all_none():
    result = options_flow.compute_options_activity(None)
    assert result["options_score"] is None
    assert result["avg_implied_volatility"] is None
    assert result["flagged_contracts"] == []


def test_avg_iv_prefers_flagged_contracts():
    contracts = [
        _contract("call", volume=5000, open_interest=100, iv=0.80),  # flagged: high vol/OI
        _contract("put", volume=10, open_interest=1000, iv=0.20),  # not flagged: low volume
    ]
    result = options_flow.compute_options_activity(contracts)
    assert result["flagged_contracts"]  # the call got flagged
    assert result["avg_implied_volatility"] == 0.80  # only the flagged contract's IV counted


def test_avg_iv_falls_back_to_whole_chain_when_nothing_flagged():
    contracts = [
        _contract("call", volume=10, open_interest=1000, iv=0.40),
        _contract("put", volume=10, open_interest=1000, iv=0.60),
    ]
    result = options_flow.compute_options_activity(contracts)
    assert result["flagged_contracts"] == []
    assert result["avg_implied_volatility"] == 0.50  # mean of both


def test_avg_iv_ignores_missing_values():
    contracts = [
        _contract("call", volume=10, open_interest=1000, iv=None),
        _contract("put", volume=10, open_interest=1000, iv=0.30),
    ]
    result = options_flow.compute_options_activity(contracts)
    assert result["avg_implied_volatility"] == 0.30
