from app.config import Settings
from app.scan.fundamentals import evaluate_fundamentals


def _settings(**overrides):
    cfg = {
        "require_positive_earnings": True,
        "require_pe_in_range": True,
        "min_pe": 0,
        "max_pe": 60,
        "require_revenue_growth": True,
        **overrides,
    }
    return Settings(raw={"scoring": {"fundamentals": cfg}})


def _fundamentals(net_income=1_000_000.0, trailing_pe=25.0, revenue_growth_yoy=0.10):
    return {
        "net_income": net_income,
        "trailing_eps": 2.5,
        "trailing_pe": trailing_pe,
        "revenue_growth_yoy": revenue_growth_yoy,
    }


def test_all_checks_pass():
    result = evaluate_fundamentals(_fundamentals(), _settings())
    assert result["fundamentals_status"] == "pass"
    assert result["fundamentals_pass"] is True


def test_none_fundamentals_is_unknown():
    result = evaluate_fundamentals(None, _settings())
    assert result["fundamentals_status"] == "unknown"
    assert result["fundamentals_pass"] is False


def test_missing_pe_is_unknown_not_fail():
    result = evaluate_fundamentals(_fundamentals(trailing_pe=None), _settings())
    assert result["fundamentals_status"] == "unknown"
    assert result["pe_in_range"] is None


def test_negative_net_income_fails():
    result = evaluate_fundamentals(_fundamentals(net_income=-500.0), _settings())
    assert result["is_profitable"] is False
    assert result["fundamentals_status"] == "fail"


def test_pe_out_of_range_fails():
    result = evaluate_fundamentals(_fundamentals(trailing_pe=200.0), _settings())
    assert result["pe_in_range"] is False
    assert result["fundamentals_status"] == "fail"


def test_negative_revenue_growth_fails():
    result = evaluate_fundamentals(_fundamentals(revenue_growth_yoy=-0.05), _settings())
    assert result["revenue_growing"] is False
    assert result["fundamentals_status"] == "fail"


def test_require_positive_earnings_can_be_disabled():
    result = evaluate_fundamentals(
        _fundamentals(net_income=-500.0), _settings(require_positive_earnings=False)
    )
    assert result["fundamentals_status"] == "pass"


def test_require_pe_in_range_can_be_disabled():
    result = evaluate_fundamentals(
        _fundamentals(trailing_pe=200.0), _settings(require_pe_in_range=False)
    )
    assert result["fundamentals_status"] == "pass"


def test_require_revenue_growth_can_be_disabled():
    result = evaluate_fundamentals(
        _fundamentals(revenue_growth_yoy=-0.05), _settings(require_revenue_growth=False)
    )
    assert result["fundamentals_status"] == "pass"


def test_nan_values_treated_as_unknown_not_fail():
    result = evaluate_fundamentals(_fundamentals(trailing_pe=float("nan")), _settings())
    assert result["pe_in_range"] is None
    assert result["fundamentals_status"] == "unknown"


def test_unprofitable_without_pe_is_fail_not_unknown():
    # Loss-making companies have no trailing P/E; the definite profitability
    # failure must win over the missing P/E (the U case: "FUND N/A" before).
    result = evaluate_fundamentals(
        _fundamentals(net_income=-300_000_000.0, trailing_pe=None, revenue_growth_yoy=0.24), _settings()
    )
    assert result["fundamentals_status"] == "fail"
    assert result["pe_in_range"] is None


def test_shrinking_revenue_with_missing_pe_is_fail():
    result = evaluate_fundamentals(_fundamentals(trailing_pe=None, revenue_growth_yoy=-0.05), _settings())
    assert result["fundamentals_status"] == "fail"
