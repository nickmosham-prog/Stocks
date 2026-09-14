from datetime import datetime, timedelta

from app.scan import breakout

NOW = datetime(2026, 9, 14, 10, 0, 0)


def test_not_broken_resets_hold_state():
    result = breakout.update_hold_state(
        level_broken=False, direction="bullish", now=NOW,
        prev_holding_since=(NOW - timedelta(minutes=20)).isoformat(), prev_direction="bullish",
    )
    assert result == {"holding_since": None, "hold_minutes": None}


def test_first_time_broken_starts_the_clock_at_zero():
    result = breakout.update_hold_state(
        level_broken=True, direction="bullish", now=NOW,
        prev_holding_since=None, prev_direction=None,
    )
    assert result["holding_since"] == NOW.isoformat()
    assert result["hold_minutes"] == 0.0


def test_same_direction_keeps_accumulating_from_original_start():
    started = NOW - timedelta(minutes=22)
    result = breakout.update_hold_state(
        level_broken=True, direction="bullish", now=NOW,
        prev_holding_since=started.isoformat(), prev_direction="bullish",
    )
    assert result["holding_since"] == started.isoformat()
    assert result["hold_minutes"] == 22.0


def test_direction_flip_restarts_the_clock():
    started = NOW - timedelta(minutes=30)
    result = breakout.update_hold_state(
        level_broken=True, direction="bearish", now=NOW,
        prev_holding_since=started.isoformat(), prev_direction="bullish",
    )
    assert result["holding_since"] == NOW.isoformat()
    assert result["hold_minutes"] == 0.0


def test_confirmation_threshold_is_caller_responsibility():
    # update_hold_state only reports elapsed time; "confirmed" is computed
    # by the caller (runner.py) against the configured threshold.
    started = NOW - timedelta(minutes=15)
    result = breakout.update_hold_state(
        level_broken=True, direction="bullish", now=NOW,
        prev_holding_since=started.isoformat(), prev_direction="bullish",
    )
    assert result["hold_minutes"] == 15.0
