"""In-process scheduler: pre-market scan, intraday scan, the daily EOD
volume-profile/trend refresh, the daily fundamentals refresh, and the Top
Picks digest emails. No external cron - this all runs inside the same
Python process as the web server (see main.py).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import load_settings, load_watchlist
from app.datasource.base import DataSource
from app.market_calendar import is_market_day, is_premarket_window, is_regular_window
from app import alerts
from app.scan import fundamentals, runner, trend, volume_profile

log = logging.getLogger(__name__)


def _premarket_job(source: DataSource) -> None:
    if not is_premarket_window():
        return
    try:
        runner.run_scan(source, "premarket")
    except Exception:
        log.exception("premarket scan failed")


def _intraday_job(source: DataSource) -> None:
    if not is_regular_window():
        return
    try:
        runner.run_scan(source, "regular")
    except Exception:
        log.exception("intraday scan failed")


def _eod_job(source: DataSource) -> None:
    if not is_market_day():
        return
    symbols = load_watchlist()
    log.info("starting EOD volume-profile refresh for %d symbols", len(symbols))
    succeeded, failed = volume_profile.refresh_historical_data(source, symbols)
    log.info("EOD refresh complete: %d succeeded, %d failed", succeeded, failed)
    log.info("trend metrics refreshed for %d symbols", trend.refresh_all(symbols))


def _fundamentals_job(source: DataSource) -> None:
    if not is_market_day():
        return
    symbols = load_watchlist()
    log.info("starting daily fundamentals refresh for %d symbols", len(symbols))
    succeeded, failed = fundamentals.refresh_all(source, symbols)
    log.info("fundamentals refresh complete: %d succeeded, %d failed", succeeded, failed)
    # Not recorded in scan_runs: its run_type CHECK constraint can't safely
    # gain a new allowed value on an existing user's database via
    # ALTER TABLE (SQLite has no ADD CONSTRAINT) - logging is enough here,
    # same as how bootstrap_if_needed already reports without a scan_runs row.


def _picks_digest_job(slot_label: str) -> None:
    if not is_market_day():
        return
    try:
        alerts.send_picks_digest(slot_label)
    except Exception:
        log.exception("Top Picks digest (%s) failed", slot_label)


def bootstrap_if_needed(source: DataSource) -> None:
    """Runs the daily refreshes synchronously on startup when their data is
    missing/stale, so day one works without a manual step. Each check is
    independent: a fresh volume profile must not skip a missing
    fundamentals load (that exact early-return kept BUY Setup from ever
    firing)."""
    symbols = load_watchlist()
    if volume_profile.needs_bootstrap(symbols) or volume_profile.daily_history_is_short(symbols):
        log.info("volume profile / daily history missing, stale or short - refreshing now (a few minutes)...")
        succeeded, failed = volume_profile.refresh_historical_data(source, symbols)
        log.info("history bootstrap complete: %d succeeded, %d failed", succeeded, failed)
    else:
        log.info("volume profile and daily history are fresh, skipping history bootstrap")

    if fundamentals.needs_bootstrap(symbols):
        log.info("fundamentals missing/stale, loading now (a few minutes)...")
        f_succeeded, f_failed = fundamentals.refresh_all(source, symbols)
        log.info("fundamentals bootstrap complete: %d succeeded, %d failed", f_succeeded, f_failed)
    else:
        log.info("fundamentals are fresh, skipping fundamentals bootstrap")

    log.info("trend metrics computed for %d symbols", trend.refresh_all(symbols))


def create_scheduler(source: DataSource) -> BackgroundScheduler:
    settings = load_settings()
    scheduler = BackgroundScheduler(timezone=settings.get("app", "timezone", default="America/New_York"))

    premarket_minutes = settings.get("schedule", "premarket_scan_minutes", default=5)
    intraday_minutes = settings.get("schedule", "intraday_scan_minutes", default=5)
    eod_time = settings.get("schedule", "eod_refresh_time", default="16:30")
    eod_hour, eod_minute = eod_time.split(":")
    fundamentals_time = settings.get("schedule", "fundamentals_refresh_time", default="17:00")
    fundamentals_hour, fundamentals_minute = fundamentals_time.split(":")

    scheduler.add_job(
        _premarket_job, IntervalTrigger(minutes=premarket_minutes), args=[source], id="premarket_scan"
    )
    scheduler.add_job(
        _intraday_job, IntervalTrigger(minutes=intraday_minutes), args=[source], id="intraday_scan"
    )
    scheduler.add_job(
        _eod_job,
        CronTrigger(day_of_week="mon-fri", hour=int(eod_hour), minute=int(eod_minute)),
        args=[source],
        id="eod_refresh",
    )
    scheduler.add_job(
        _fundamentals_job,
        CronTrigger(day_of_week="mon-fri", hour=int(fundamentals_hour), minute=int(fundamentals_minute)),
        args=[source],
        id="fundamentals_refresh",
    )
    if settings.get("picks", "enabled", default=True):
        for slot in settings.get("picks", "digest_times", default=["10:00", "15:00"]):
            hour, minute = str(slot).split(":")
            scheduler.add_job(
                _picks_digest_job,
                CronTrigger(day_of_week="mon-fri", hour=int(hour), minute=int(minute)),
                args=[str(slot)],
                id=f"picks_digest_{hour}{minute}",
                misfire_grace_time=1800,
                coalesce=True,
            )
    return scheduler
