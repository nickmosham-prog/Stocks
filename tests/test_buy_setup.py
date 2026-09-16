from app.config import Settings
from app.scan.buy_setup import meets_buy_setup_criteria


def _settings(**buy_overrides):
    buy_cfg = {
        "enabled": True,
        "alpha_score_threshold": 88,
        "require_bullish_direction": True,
        "require_confirmed_breakout": True,
        "require_fundamentals": True,
        **buy_overrides,
    }
    return Settings(raw={"alerts": {"buy_setup": buy_cfg}})


def _row(alpha_score=95.0, breakout_confirmed=1, breakout_direction="bullish", fundamentals_status="pass"):
    return {
        "alpha_score": alpha_score,
        "breakout_confirmed": breakout_confirmed,
        "breakout_direction": breakout_direction,
        "fundamentals_status": fundamentals_status,
    }


def test_meets_criteria_when_all_conditions_satisfied():
    assert meets_buy_setup_criteria(_row(), _settings()) is True


def test_fails_when_tier_disabled():
    assert meets_buy_setup_criteria(_row(), _settings(enabled=False)) is False


def test_fails_below_threshold():
    assert meets_buy_setup_criteria(_row(alpha_score=80.0), _settings()) is False


def test_fails_on_nan_alpha_score():
    assert meets_buy_setup_criteria(_row(alpha_score=float("nan")), _settings()) is False


def test_fails_when_breakout_not_confirmed():
    assert meets_buy_setup_criteria(_row(breakout_confirmed=0), _settings()) is False


def test_fails_when_direction_is_bearish():
    assert meets_buy_setup_criteria(_row(breakout_direction="bearish"), _settings()) is False


def test_confirmed_requirement_can_be_relaxed_via_settings():
    row = _row(breakout_confirmed=0)
    assert meets_buy_setup_criteria(row, _settings(require_confirmed_breakout=False)) is True


def test_bullish_requirement_can_be_relaxed_via_settings():
    row = _row(breakout_direction="bearish")
    assert meets_buy_setup_criteria(row, _settings(require_bullish_direction=False)) is True


def test_fails_when_fundamentals_status_is_fail():
    assert meets_buy_setup_criteria(_row(fundamentals_status="fail"), _settings()) is False


def test_fails_when_fundamentals_status_is_unknown():
    # Fail-closed: missing/unverifiable fundamentals never earn a BUY badge.
    assert meets_buy_setup_criteria(_row(fundamentals_status="unknown"), _settings()) is False


def test_fundamentals_requirement_can_be_relaxed_via_settings():
    row = _row(fundamentals_status="fail")
    assert meets_buy_setup_criteria(row, _settings(require_fundamentals=False)) is True
