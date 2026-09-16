from app.config import Settings
from app.scan.breakdown_setup import meets_breakdown_setup_criteria


def _settings(**overrides):
    cfg = {
        "enabled": True,
        "alpha_score_threshold": 88,
        "require_confirmed_breakout": True,
        "require_bearish_direction": True,
        **overrides,
    }
    return Settings(raw={"alerts": {"breakdown_setup": cfg}})


def _row(alpha_score=95.0, breakout_confirmed=1, breakout_direction="bearish"):
    return {
        "alpha_score": alpha_score,
        "breakout_confirmed": breakout_confirmed,
        "breakout_direction": breakout_direction,
    }


def test_meets_criteria_when_all_conditions_satisfied():
    assert meets_breakdown_setup_criteria(_row(), _settings()) is True


def test_fails_when_tier_disabled():
    assert meets_breakdown_setup_criteria(_row(), _settings(enabled=False)) is False


def test_fails_below_threshold():
    assert meets_breakdown_setup_criteria(_row(alpha_score=80.0), _settings()) is False


def test_fails_when_breakout_not_confirmed():
    assert meets_breakdown_setup_criteria(_row(breakout_confirmed=0), _settings()) is False


def test_fails_when_direction_is_bullish():
    assert meets_breakdown_setup_criteria(_row(breakout_direction="bullish"), _settings()) is False


def test_no_fundamentals_gate_applies():
    # Unlike buy_setup, a missing/failing fundamentals_status must not
    # block a breakdown signal - there's no such gate here at all.
    row = _row()
    row["fundamentals_status"] = "fail"
    assert meets_breakdown_setup_criteria(row, _settings()) is True
