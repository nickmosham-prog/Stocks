"""In-process scheduler: pre-market scan, intraday scan, the daily EOD
volume-profile/trend refresh, the daily fundamentals refresh, and the Top
Picks digest emails. No external cron - this all runs inside the same
Python process as the web server (see main.py).
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import load_settings, load_watchlist
from app.datasource.base import DataSource
from app.market_calendar import is_market_day, is_premarket_window, is_regular_window
from app import alerts, maintenance
from app.scan import fundamentals, runner, scorecard, trend, volume_profile

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
    _scorecard_job(source)
    maintenance.prune_old_rows()


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


def _scorecard_job(source: DataSource) -> None:
    """Fills in how past picks have done since (stock results from the
    daily bars; option prices only while the market is open)."""
    try:
        result = scorecard.update_outcomes(source)
        log.info("scorecard updated: %d stock results, %d option prices", result["stocks"], result["options"])
    except Exception:
        log.exception("scorecard update failed")


def _picks_digest_job(slot_label: str, source: DataSource) -> None:
    if not is_market_day():
        return
    if is_regular_window():
        # The digest must never go out on stale data (e.g. right after
        # startup or after the Mac wakes): scan first if nothing is fresh.
        try:
            if runner.ensure_fresh_scan(source, "regular", alerts.DIGEST_MAX_SCAN_AGE_MINUTES):
                log.info("ran a scan for the %s Top Picks digest (no fresh scan was available)", slot_label)
        except Exception:
            log.exception("scan for the %s Top Picks digest failed", slot_label)
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

    # next_run_time=now: scan as soon as the scheduler starts instead of
    # waiting one full interval (each job checks its own session window).
    now = datetime.now(tz=scheduler.timezone)
    scheduler.add_job(
        _premarket_job, IntervalTrigger(minutes=premarket_minutes), args=[source], id="premarket_scan",
        next_run_time=now,
    )
    scheduler.add_job(
        _intraday_job, IntervalTrigger(minutes=intraday_minutes), args=[source], id="intraday_scan",
        next_run_time=now,
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
    # Scorecard: once at startup (after the history bootstrap), and shortly
    # before the close each day - the app is often stopped at 4pm, before
    # the EOD refresh would otherwise do it.
    scheduler.add_job(_scorecard_job, args=[source], id="scorecard_startup", next_run_time=now)
    scheduler.add_job(
        _scorecard_job,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=45),
        args=[source],
        id="scorecard_update",
        misfire_grace_time=600,
    )
    if settings.get("picks", "enabled", default=True):
        for slot in settings.get("picks", "digest_times", default=["10:00", "15:00"]):
            hour, minute = str(slot).split(":")
            scheduler.add_job(
                _picks_digest_job,
                CronTrigger(day_of_week="mon-fri", hour=int(hour), minute=int(minute)),
                args=[str(slot), source],
                id=f"picks_digest_{hour}{minute}",
                misfire_grace_time=1800,
                coalesce=True,
            )
    return scheduler
