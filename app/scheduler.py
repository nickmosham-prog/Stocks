"""In-process scheduler: pre-market scan, intraday scan, and the daily EOD
volume-profile refresh. No external cron - this all runs inside the same
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
from app.scan import runner, volume_profile

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


def bootstrap_if_needed(source: DataSource) -> None:
    """Runs the EOD refresh synchronously on startup if the historical
    volume profile is missing/stale, so day one works without a manual step."""
    symbols = load_watchlist()
    if not volume_profile.needs_bootstrap(symbols):
        log.info("volume profile is fresh, skipping startup bootstrap")
        return
    log.info("volume profile missing/stale, bootstrapping now (this can take a few minutes)...")
    succeeded, failed = volume_profile.refresh_historical_data(source, symbols)
    log.info("bootstrap complete: %d succeeded, %d failed", succeeded, failed)


def create_scheduler(source: DataSource) -> BackgroundScheduler:
    settings = load_settings()
    scheduler = BackgroundScheduler(timezone=settings.get("app", "timezone", default="America/New_York"))

    premarket_minutes = settings.get("schedule", "premarket_scan_minutes", default=5)
    intraday_minutes = settings.get("schedule", "intraday_scan_minutes", default=5)
    eod_time = settings.get("schedule", "eod_refresh_time", default="16:30")
    eod_hour, eod_minute = eod_time.split(":")

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
    return scheduler
