"""Regression tests for the review fixes: overnight hold reset, data
freshness by market close, scan-before-digest, retention, scan health."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import alerts, maintenance, market_calendar, scheduler
from app.scan import breakout, runner, volume_profile

ET = ZoneInfo("America/New_York")


def _et(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ET)


# --- hold timer --------------------------------------------------------------

def test_hold_from_a_previous_day_restarts_the_clock():
    # Broke out at 10:00 yesterday; at 7:05 today the level is a new one
    # (yesterday's high), so the clock must restart instead of reading ~21 hours.
    now = _et(2026, 11, 10, 7, 5)
    result = breakout.update_hold_state(
        level_broken=True, direction="bullish", now=now,
        prev_holding_since=_et(2026, 11, 9, 10, 0).isoformat(), prev_direction="bullish",
    )
    assert result["hold_minutes"] == 0.0
    assert result["holding_since"] == now.isoformat()


def test_hold_from_earlier_today_keeps_accumulating():
    now = _et(2026, 11, 10, 10, 30)
    result = breakout.update_hold_state(
        level_broken=True, direction="bullish", now=now,
        prev_holding_since=_et(2026, 11, 10, 9, 45).isoformat(), prev_direction="bullish",
    )
    assert result["hold_minutes"] == 45.0


# --- last market close -------------------------------------------------------

def test_last_market_close_cases():
    close = market_calendar.last_market_close
    # Tuesday morning -> Monday's close
    assert close(_et(2026, 11, 10, 7, 0)) == _et(2026, 11, 9, 16, 0)
    # Tuesday during the session -> still Monday's close
    assert close(_et(2026, 11, 10, 15, 59)) == _et(2026, 11, 9, 16, 0)
    # Tuesday after the close -> Tuesday's close
    assert close(_et(2026, 11, 10, 16, 30)) == _et(2026, 11, 10, 16, 0)
    # Monday morning and Sunday -> Friday's close
    assert close(_et(2026, 11, 9, 7, 0)) == _et(2026, 11, 6, 16, 0)
    assert close(_et(2026, 11, 8, 12, 0)) == _et(2026, 11, 6, 16, 0)


# --- history freshness -------------------------------------------------------

def _seed_profile(db, symbols, updated_at):
    with db.cursor() as cur:
        for symbol in symbols:
            cur.execute(
                "INSERT INTO historical_volume_profile (symbol, bucket_index, session, avg_volume, stdev_volume, "
                "sample_days, updated_at) VALUES (?, 0, 'regular', 1000, 0, 20, ?)",
                (symbol, updated_at.astimezone(timezone.utc).isoformat()),
            )


def test_late_start_then_early_start_triggers_refresh(temp_db, monkeypatch):
    # Refreshed Monday 11:00 (before Monday's close, so Monday's bar is
    # missing). Tuesday 6:50 is under 20 hours later - the old rule called
    # that fresh and left "the prior day" as Friday.
    _seed_profile(temp_db, ["AAPL", "MSFT"], _et(2026, 11, 9, 11, 0))
    monkeypatch.setattr(volume_profile, "last_market_close", lambda: _et(2026, 11, 9, 16, 0))
    assert volume_profile.needs_bootstrap(["AAPL", "MSFT"]) is True


def test_refresh_after_the_close_is_fresh_next_morning_and_over_a_weekend(temp_db, monkeypatch):
    _seed_profile(temp_db, ["AAPL", "MSFT"], _et(2026, 11, 6, 16, 30))  # Friday EOD refresh
    monkeypatch.setattr(volume_profile, "last_market_close", lambda: _et(2026, 11, 6, 16, 0))
    assert volume_profile.needs_bootstrap(["AAPL", "MSFT"]) is False  # Monday morning: still Friday's close


def test_no_profile_at_all_needs_bootstrap(temp_db):
    assert volume_profile.needs_bootstrap(["AAPL"]) is True


# --- scan before digest ------------------------------------------------------

def _seed_pick_run(db, scan_ts):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO pick_runs (scan_ts, session, candidates, picks_count) VALUES (?, 'regular', 3, 1)",
            (scan_ts.isoformat(),),
        )


def test_ensure_fresh_scan_runs_only_when_stale(temp_db, monkeypatch):
    now = _et(2026, 11, 10, 15, 0)
    monkeypatch.setattr(runner, "now_et", lambda: now)
    ran = []
    monkeypatch.setattr(runner, "_run_scan", lambda source, session: ran.append(session))

    assert runner.ensure_fresh_scan(None, "regular", 20) is True  # nothing recorded yet
    _seed_pick_run(temp_db, now - timedelta(minutes=45))
    assert runner.ensure_fresh_scan(None, "regular", 20) is True  # stale
    _seed_pick_run(temp_db, now - timedelta(minutes=3))
    assert runner.ensure_fresh_scan(None, "regular", 20) is False  # fresh
    assert ran == ["regular", "regular"]


def test_digest_job_scans_first_during_market_hours(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "is_market_day", lambda: True)
    monkeypatch.setattr(scheduler, "is_regular_window", lambda: True)
    monkeypatch.setattr(
        scheduler.runner, "ensure_fresh_scan", lambda source, session, age: calls.append(("scan", session, age)) or True
    )
    monkeypatch.setattr(scheduler.alerts, "send_picks_digest", lambda slot: calls.append(("send", slot)))
    scheduler._picks_digest_job("15:00", source=None)
    assert calls == [("scan", "regular", alerts.DIGEST_MAX_SCAN_AGE_MINUTES), ("send", "15:00")]


def test_digest_job_still_sends_if_the_scan_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "is_market_day", lambda: True)
    monkeypatch.setattr(scheduler, "is_regular_window", lambda: True)

    def boom(*args):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(scheduler.runner, "ensure_fresh_scan", boom)
    monkeypatch.setattr(scheduler.alerts, "send_picks_digest", lambda slot: calls.append(slot))
    scheduler._picks_digest_job("10:00", source=None)
    assert calls == ["10:00"]


def test_digest_job_does_not_scan_outside_market_hours(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "is_market_day", lambda: True)
    monkeypatch.setattr(scheduler, "is_regular_window", lambda: False)
    monkeypatch.setattr(scheduler.runner, "ensure_fresh_scan", lambda *a: calls.append("scan"))
    monkeypatch.setattr(scheduler.alerts, "send_picks_digest", lambda slot: calls.append("send"))
    scheduler._picks_digest_job("10:00", source=None)
    assert calls == ["send"]


def test_scans_are_scheduled_to_run_at_startup():
    sched = scheduler.create_scheduler(source=None)
    for job_id in ("premarket_scan", "intraday_scan"):
        job = sched.get_job(job_id)
        assert job.next_run_time is not None
        assert abs((job.next_run_time - datetime.now(tz=job.next_run_time.tzinfo)).total_seconds()) < 60


# --- retention ---------------------------------------------------------------

def test_prune_removes_old_history_and_keeps_recent(temp_db, monkeypatch):
    now = _et(2026, 11, 10, 9, 0)
    monkeypatch.setattr(maintenance, "now_et", lambda: now)
    old, recent = now - timedelta(days=45), now - timedelta(days=5)
    with temp_db.cursor() as cur:
        for ts in (old, recent):
            cur.execute(
                "INSERT INTO scan_snapshots (symbol, session, scan_ts, price) VALUES ('AAPL', 'regular', ?, 100)",
                (ts.isoformat(),),
            )
            cur.execute(
                "INSERT INTO pick_runs (scan_ts, session, candidates, picks_count) VALUES (?, 'regular', 1, 1)",
                (ts.isoformat(),),
            )
            cur.execute(
                "INSERT INTO scan_runs (run_type, started_at) VALUES ('intraday', ?)",
                (ts.astimezone(timezone.utc).isoformat(),),
            )
        cur.execute(
            "INSERT INTO daily_ohlc (symbol, trade_date, close) VALUES ('AAPL', ?, 100)", (old.strftime("%Y-%m-%d"),)
        )

    assert maintenance.prune_old_rows(days=30) == 3
    with temp_db.cursor() as cur:
        for table in ("scan_snapshots", "pick_runs", "scan_runs"):
            assert cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1, table
        assert cur.execute("SELECT COUNT(*) FROM daily_ohlc").fetchone()[0] == 1  # price history is never pruned
    assert maintenance.prune_old_rows(days=0) == 0  # 0 disables pruning


# --- scan health in /api/status ----------------------------------------------

def test_status_reports_the_last_finished_scan(temp_db):
    from fastapi.testclient import TestClient

    from app.web.server import create_app

    client = TestClient(create_app())
    assert client.get("/api/status").json()["last_scan"] is None
    with temp_db.cursor() as cur:
        cur.execute(
            "INSERT INTO scan_runs (run_type, started_at, finished_at, tickers_attempted, tickers_failed) "
            "VALUES ('intraday', '2026-11-10T15:00:00+00:00', '2026-11-10T15:00:40+00:00', 148, 2)"
        )
        cur.execute("INSERT INTO scan_runs (run_type, started_at) VALUES ('intraday', '2026-11-10T15:05:00+00:00')")
    last = client.get("/api/status").json()["last_scan"]
    assert last["finished_at"] == "2026-11-10T15:00:40+00:00"  # the unfinished run is ignored
    assert last["tickers_failed"] == 2
