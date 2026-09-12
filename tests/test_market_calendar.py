import datetime as dt
from zoneinfo import ZoneInfo

from app import market_calendar as mc

ET = ZoneInfo("America/New_York")


def _et(y, m, d, h, minute):
    return dt.datetime(y, m, d, h, minute, tzinfo=ET)


def test_is_market_day_weekday_vs_weekend():
    monday = _et(2024, 1, 1, 9, 0)  # 2024-01-01 is a Monday
    saturday = _et(2024, 1, 6, 9, 0)  # 2024-01-06 is a Saturday
    assert mc.is_market_day(monday) is True
    assert mc.is_market_day(saturday) is False


def test_premarket_and_regular_windows():
    monday_premarket = _et(2024, 1, 1, 8, 0)
    monday_regular = _et(2024, 1, 1, 10, 0)
    monday_after_close = _et(2024, 1, 1, 17, 0)

    assert mc.is_premarket_window(monday_premarket) is True
    assert mc.is_regular_window(monday_premarket) is False

    assert mc.is_regular_window(monday_regular) is True
    assert mc.is_premarket_window(monday_regular) is False

    assert mc.is_premarket_window(monday_after_close) is False
    assert mc.is_regular_window(monday_after_close) is False


def test_windows_are_false_on_weekends():
    saturday_during_regular_hours = _et(2024, 1, 6, 10, 0)
    assert mc.is_premarket_window(saturday_during_regular_hours) is False
    assert mc.is_regular_window(saturday_during_regular_hours) is False


def test_current_session():
    assert mc.current_session(_et(2024, 1, 1, 8, 0)) == "premarket"
    assert mc.current_session(_et(2024, 1, 1, 10, 0)) == "regular"
    assert mc.current_session(_et(2024, 1, 1, 20, 0)) is None
    assert mc.current_session(_et(2024, 1, 6, 10, 0)) is None  # Saturday


def test_bucket_index_premarket():
    # premarket window starts 07:00, default bucket size 5 minutes
    moment = _et(2024, 1, 1, 7, 12)
    assert mc.bucket_index(moment, "premarket", 5) == 2


def test_bucket_index_regular():
    # regular window starts 09:30
    moment = _et(2024, 1, 1, 9, 37)
    assert mc.bucket_index(moment, "regular", 5) == 1


def test_num_buckets():
    assert mc.num_buckets("premarket", 5) == 30  # 07:00-09:30 = 150 min / 5
    assert mc.num_buckets("regular", 5) == 78  # 09:30-16:00 = 390 min / 5
