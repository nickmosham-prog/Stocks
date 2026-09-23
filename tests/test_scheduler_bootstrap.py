from app import scheduler


def _patch(monkeypatch, profile_needs=False, history_short=False, fundamentals_needs=True):
    calls = []
    monkeypatch.setattr(scheduler, "load_watchlist", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr(scheduler.volume_profile, "needs_bootstrap", lambda s: profile_needs)
    monkeypatch.setattr(scheduler.volume_profile, "daily_history_is_short", lambda s: history_short)
    monkeypatch.setattr(
        scheduler.volume_profile, "refresh_historical_data", lambda src, s: calls.append("history") or (2, 0)
    )
    monkeypatch.setattr(scheduler.fundamentals, "needs_bootstrap", lambda s: fundamentals_needs)
    monkeypatch.setattr(scheduler.fundamentals, "refresh_all", lambda src, s: calls.append("fundamentals") or (2, 0))
    monkeypatch.setattr(scheduler.trend, "refresh_all", lambda s: calls.append("trend") or 2)
    return calls


def test_fundamentals_load_even_when_volume_profile_is_fresh(monkeypatch):
    # Regression: a fresh volume profile used to return early and skip the
    # fundamentals load, so BUY Setup could never pass.
    calls = _patch(monkeypatch, profile_needs=False, fundamentals_needs=True)
    scheduler.bootstrap_if_needed(source=None)
    assert calls == ["fundamentals", "trend"]


def test_short_daily_history_triggers_history_refresh(monkeypatch):
    calls = _patch(monkeypatch, profile_needs=False, history_short=True, fundamentals_needs=False)
    scheduler.bootstrap_if_needed(source=None)
    assert calls == ["history", "trend"]


def test_everything_fresh_only_recomputes_trend(monkeypatch):
    calls = _patch(monkeypatch, fundamentals_needs=False)
    scheduler.bootstrap_if_needed(source=None)
    assert calls == ["trend"]


def test_digest_jobs_registered(monkeypatch):
    sched = scheduler.create_scheduler(source=None)
    ids = {job.id for job in sched.get_jobs()}
    assert {"picks_digest_1000", "picks_digest_1500"} <= ids
