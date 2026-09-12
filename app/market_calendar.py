"""Market day / session-window checks (no holiday calendar in v1 - see README)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import load_settings


def _tz() -> ZoneInfo:
    return ZoneInfo(load_settings().get("app", "timezone", default="America/New_York"))


def now_et() -> dt.datetime:
    return dt.datetime.now(tz=_tz())


def is_market_day(moment: dt.datetime | None = None) -> bool:
    """Monday-Friday check only. No holiday calendar in v1 (see README limitations)."""
    moment = moment or now_et()
    return moment.weekday() < 5  # 0=Monday ... 4=Friday


def _parse_hhmm(value: str) -> dt.time:
    hour, minute = value.split(":")
    return dt.time(hour=int(hour), minute=int(minute))


def _in_window(moment: dt.datetime, start: str, end: str) -> bool:
    t = moment.timetz().replace(tzinfo=None)
    return _parse_hhmm(start) <= t < _parse_hhmm(end)


def is_premarket_window(moment: dt.datetime | None = None) -> bool:
    moment = moment or now_et()
    if not is_market_day(moment):
        return False
    settings = load_settings()
    return _in_window(
        moment,
        settings.get("schedule", "premarket_window_start", default="07:00"),
        settings.get("schedule", "premarket_window_end", default="09:30"),
    )


def is_regular_window(moment: dt.datetime | None = None) -> bool:
    moment = moment or now_et()
    if not is_market_day(moment):
        return False
    settings = load_settings()
    return _in_window(
        moment,
        settings.get("schedule", "intraday_window_start", default="09:30"),
        settings.get("schedule", "intraday_window_end", default="16:00"),
    )


def current_session(moment: dt.datetime | None = None) -> str | None:
    """Returns 'premarket', 'regular', or None if outside both windows."""
    moment = moment or now_et()
    if is_premarket_window(moment):
        return "premarket"
    if is_regular_window(moment):
        return "regular"
    return None


def session_window(session: str) -> tuple[dt.time, dt.time]:
    settings = load_settings()
    if session == "premarket":
        return (
            _parse_hhmm(settings.get("schedule", "premarket_window_start", default="07:00")),
            _parse_hhmm(settings.get("schedule", "premarket_window_end", default="09:30")),
        )
    if session == "regular":
        return (
            _parse_hhmm(settings.get("schedule", "intraday_window_start", default="09:30")),
            _parse_hhmm(settings.get("schedule", "intraday_window_end", default="16:00")),
        )
    raise ValueError(f"unknown session: {session}")


def bucket_index(moment: dt.datetime, session: str, bucket_minutes: int) -> int:
    """Which time-of-day bucket `moment` falls into within `session`'s window."""
    start, _ = session_window(session)
    t = moment.timetz().replace(tzinfo=None)
    minutes_since_start = (t.hour * 60 + t.minute) - (start.hour * 60 + start.minute)
    return max(0, minutes_since_start // bucket_minutes)


def num_buckets(session: str, bucket_minutes: int) -> int:
    start, end = session_window(session)
    total_minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return total_minutes // bucket_minutes
