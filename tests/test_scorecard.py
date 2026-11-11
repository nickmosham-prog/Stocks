from datetime import date, timedelta

import pytest

from app import maintenance
from app.scan import scorecard

PICK_DATE = "2026-11-02"  # a Monday


def _bars(closes, start=date(2026, 11, 2), highs=None, lows=None):
    """Daily bars on consecutive weekdays starting at `start` (the pick day)."""
    bars, day = [], start
    for i, close in enumerate(closes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        bars.append({
            "trade_date": day.isoformat(), "close": close,
            "high": highs[i] if highs else close * 1.005, "low": lows[i] if lows else close * 0.995,
        })
        day += timedelta(days=1)
    return bars


# --- stock outcomes ----------------------------------------------------------

def test_returns_are_measured_from_the_entry_price_at_each_horizon():
    closes = [100.0] + [101.0, 102.0, 103.0, 104.0, 105.0] + [106.0] * 14 + [110.0]  # pick day + 20 days
    spy = [500.0] + [501.0] * 4 + [505.0] + [505.0] * 15
    out = scorecard.compute_stock_outcome(PICK_DATE, 100.0, _bars(closes), 500.0, _bars(spy))
    assert out["days_elapsed"] == 20
    assert out["ret_1d_pct"] == pytest.approx(1.0)
    assert out["ret_5d_pct"] == pytest.approx(5.0)
    assert out["ret_20d_pct"] == pytest.approx(10.0)
    assert out["spy_ret_5d_pct"] == pytest.approx(1.0)
    assert out["ret_latest_pct"] == pytest.approx(10.0)


def test_horizons_not_reached_yet_stay_empty():
    out = scorecard.compute_stock_outcome(PICK_DATE, 100.0, _bars([100.0, 102.0, 101.0]), 500.0, [])
    assert out["days_elapsed"] == 2
    assert out["ret_1d_pct"] == pytest.approx(2.0)
    assert out["ret_5d_pct"] is None and out["ret_20d_pct"] is None
    assert out["spy_ret_1d_pct"] is None
    assert out["ret_latest_pct"] == pytest.approx(1.0)


def test_on_the_pick_day_itself_only_the_latest_return_exists():
    out = scorecard.compute_stock_outcome(PICK_DATE, 100.0, _bars([103.0]))
    assert out["days_elapsed"] == 0 and out["ret_1d_pct"] is None
    assert out["ret_latest_pct"] == pytest.approx(3.0)


def test_target_reached_before_the_stop():
    bars = _bars([100, 102, 104, 107, 95], highs=[100, 102.5, 104.5, 107.5, 96], lows=[99, 101, 103, 105, 94])
    out = scorecard.compute_stock_outcome(PICK_DATE, 100.0, bars, stop_price=97.0, target_price=106.0)
    assert (out["outcome"], out["outcome_date"]) == ("target", bars[3]["trade_date"])


def test_stop_reached_first_and_a_day_touching_both_counts_as_stopped():
    bars = _bars([100, 99, 96], highs=[100, 100, 97], lows=[99.5, 98, 95])
    out = scorecard.compute_stock_outcome(PICK_DATE, 100.0, bars, stop_price=97.0, target_price=106.0)
    assert (out["outcome"], out["outcome_date"]) == ("stopped", bars[2]["trade_date"])

    wild = _bars([100, 100], highs=[100, 107], lows=[99.5, 96])
    out = scorecard.compute_stock_outcome(PICK_DATE, 100.0, wild, stop_price=97.0, target_price=106.0)
    assert out["outcome"] == "stopped"


def test_the_pick_days_own_range_is_ignored_for_stop_and_target():
    # The low on the pick day was before the pick was made - it can't stop it out.
    bars = _bars([100, 101], highs=[108, 101.5], lows=[95, 100.5])
    out = scorecard.compute_stock_outcome(PICK_DATE, 100.0, bars, stop_price=97.0, target_price=106.0)
    assert out["outcome"] is None


# --- logging -----------------------------------------------------------------

def _record(symbol="GOOD", scan_ts="2026-11-02T10:00:00-05:00", **overrides):
    record = {
        "scan_ts": scan_ts, "rank": 1, "symbol": symbol, "price": 100.0, "pick_score": 75.0, "sector": "Technology",
        "stop_price": 97.0, "target_price": 106.0, "contract_symbol": "GOODC95", "option_type": "call",
        "strike": 95.0, "expiration": "2026-12-18", "option_price": 8.0,
    }
    record.update(overrides)
    return record


def test_a_pick_is_logged_once_per_day_and_only_in_the_regular_session(temp_db):
    assert scorecard.log_picks([_record()], "premarket", 500.0, "healthy") == 0
    assert scorecard.log_picks([_record()], "regular", 500.0, "healthy") == 1
    later = _record(scan_ts="2026-11-02T14:00:00-05:00", price=104.0)
    assert scorecard.log_picks([later], "regular", 503.0, "healthy") == 0   # same day: first entry stands
    next_day = _record(scan_ts="2026-11-03T10:00:00-05:00", price=105.0)
    assert scorecard.log_picks([next_day], "regular", 504.0, "caution") == 1
    rows = scorecard.load_rows()
    assert [(r["pick_date"], r["entry_price"], r["spy_entry_price"]) for r in rows] == [
        ("2026-11-03", 105.0, 504.0), ("2026-11-02", 100.0, 500.0),
    ]


def _seed_daily(db, symbol, bars):
    with db.cursor() as cur:
        for b in bars:
            cur.execute(
                "INSERT INTO daily_ohlc (symbol, trade_date, high, low, close) VALUES (?, ?, ?, ?, ?)",
                (symbol, b["trade_date"], b["high"], b["low"], b["close"]),
            )


def test_update_fills_results_from_daily_bars_and_keeps_a_decided_outcome(temp_db):
    scorecard.log_picks([_record()], "regular", 500.0, "healthy")
    _seed_daily(temp_db, "GOOD", _bars([100, 102, 107], highs=[100, 102.5, 107.5], lows=[99.5, 101, 103]))
    _seed_daily(temp_db, "SPY", _bars([500, 502, 503]))
    assert scorecard.update_stock_outcomes() == 1
    row = scorecard.load_rows()[0]
    assert row["ret_1d_pct"] == pytest.approx(2.0) and row["spy_ret_1d_pct"] == pytest.approx(0.4)
    assert row["outcome"] == "target" and row["days_elapsed"] == 2

    # A later collapse must not rewrite "target hit" as "stopped".
    with temp_db.cursor() as cur:
        cur.execute("INSERT INTO daily_ohlc (symbol, trade_date, high, low, close) VALUES ('GOOD', '2026-11-05', 99, 90, 91)")
    scorecard.update_stock_outcomes()
    row = scorecard.load_rows()[0]
    assert row["outcome"] == "target" and row["days_elapsed"] == 3
    assert row["ret_latest_pct"] == pytest.approx(-9.0)


def test_pruning_never_touches_the_scorecard(temp_db):
    scorecard.log_picks([_record(scan_ts="2025-01-06T10:00:00-05:00")], "regular", 500.0, "healthy")
    maintenance.prune_old_rows(days=30)
    assert len(scorecard.load_rows()) == 1


# --- option outcomes ---------------------------------------------------------

def test_mark_prefers_the_bid_ask_midpoint():
    assert scorecard._mark({"bid": 4.0, "ask": 4.4, "last_price": 9.9}) == pytest.approx(4.2)
    assert scorecard._mark({"bid": None, "ask": 4.4, "last_price": 3.9}) == 3.9
    assert scorecard._mark({"bid": 0, "ask": 0, "last_price": 0}) is None


def test_option_checkpoints_are_set_once_at_the_first_update_on_or_after_each_day():
    row = {"pick_date": PICK_DATE, "option_entry_price": 8.0, "option_ret_5d_pct": None,
           "option_ret_10d_pct": None, "option_ret_20d_pct": None}
    early = scorecard.option_updates(row, 9.0, date(2026, 11, 4), "t")   # 2 trading days in
    assert early == {"option_last_price": 9.0, "option_last_checked": "t"}
    day5 = scorecard.option_updates(row, 10.0, date(2026, 11, 9), "t")   # 5 trading days in
    assert day5["option_ret_5d_pct"] == pytest.approx(25.0) and "option_ret_10d_pct" not in day5
    row["option_ret_5d_pct"] = 25.0
    day6 = scorecard.option_updates(row, 4.0, date(2026, 11, 10), "t")
    assert "option_ret_5d_pct" not in day6 and day6["option_last_price"] == 4.0
    assert scorecard.option_updates(row, None, date(2026, 11, 10), "t") == {}


class _ChainSource:
    def __init__(self):
        self.calls = []

    def get_option_chain(self, symbol, expirations=None):
        self.calls.append((symbol, tuple(expirations)))
        return [
            {"option_type": "put", "strike": 95.0, "bid": 1.0, "ask": 1.2, "last_price": 1.1},
            {"option_type": "call", "strike": 95.0, "bid": 9.8, "ask": 10.2, "last_price": 9.0},
        ]


def test_option_prices_come_from_the_matching_contract_in_the_chain(temp_db):
    scorecard.log_picks([_record()], "regular", 500.0, "healthy")
    scorecard.log_picks([_record("NOOPT", contract_symbol=None, option_price=None, strike=None)], "regular", 500.0, "healthy")
    source = _ChainSource()
    assert scorecard.update_option_outcomes(source, today=date(2026, 11, 9), pause_seconds=0) == 1
    assert source.calls == [("GOOD", ("2026-12-18",))]
    row = next(r for r in scorecard.load_rows() if r["symbol"] == "GOOD")
    assert row["option_last_price"] == pytest.approx(10.0)          # call midpoint, not the put
    assert row["option_ret_5d_pct"] == pytest.approx(25.0)


def test_moomoo_coded_contracts_are_priced_in_one_batch(temp_db):
    scorecard.log_picks([_record(contract_symbol="US.GOOD261218C95000")], "regular", 500.0, "healthy")

    class Batch(_ChainSource):
        def get_option_marks(self, codes):
            self.calls.append(("batch", tuple(codes)))
            return {"US.GOOD261218C95000": 12.0}

    source = Batch()
    scorecard.update_option_outcomes(source, today=date(2026, 11, 3), pause_seconds=0)
    assert source.calls == [("batch", ("US.GOOD261218C95000",))]   # no chain lookup needed
    assert scorecard.load_rows()[0]["option_last_price"] == 12.0


def test_expired_or_long_past_options_are_not_tracked(temp_db):
    scorecard.log_picks([_record()], "regular", 500.0, "healthy")
    source = _ChainSource()
    assert scorecard.update_option_outcomes(source, today=date(2026, 12, 19), pause_seconds=0) == 0  # expired
    assert scorecard.update_option_outcomes(source, today=date(2026, 12, 10), pause_seconds=0) == 0  # past 20 days
    assert source.calls == []


# --- reporting ---------------------------------------------------------------

def _result(ret_5d, spy_5d=1.0, option_5d=None, outcome=None):
    return {"ret_1d_pct": 0.5, "ret_5d_pct": ret_5d, "ret_20d_pct": None, "spy_ret_5d_pct": spy_5d,
            "option_ret_5d_pct": option_5d, "outcome": outcome}


def test_summary_numbers():
    rows = [_result(4.0, option_5d=30.0, outcome="target"), _result(-2.0, option_5d=-20.0, outcome="stopped"),
            _result(1.0), _result(None)]
    s = scorecard.summarize(rows)
    assert s["total_picks"] == 4 and s["with_5d_result"] == 3
    assert s["win_rate_5d_pct"] == pytest.approx(66.67, abs=0.01)
    assert s["avg_ret_5d_pct"] == pytest.approx(1.0)
    assert s["avg_excess_5d_pct"] == pytest.approx(0.0)
    assert s["avg_option_ret_5d_pct"] == pytest.approx(5.0) and s["options_with_5d_result"] == 2
    assert (s["targets_hit"], s["stopped_out"]) == (1, 1)
    assert scorecard.summarize([])["win_rate_5d_pct"] is None


def test_track_record_line_waits_for_enough_results(monkeypatch):
    monkeypatch.setattr(scorecard, "load_rows", lambda limit=500: [_result(2.0)] * 4)
    assert scorecard.track_record_line() is None
    monkeypatch.setattr(scorecard, "load_rows", lambda limit=500: [_result(2.0)] * 4 + [_result(-3.0)])
    assert scorecard.track_record_line() == (
        "Track record so far: 5 picks with 5-day results, average +1.0% "
        "(S&P 500 +1.0% over the same days), 80% were up. See the Scorecard tab."
    )


def test_scorecard_api(temp_db):
    from fastapi.testclient import TestClient

    from app.web.server import create_app

    scorecard.log_picks([_record()], "regular", 500.0, "healthy")
    data = TestClient(create_app()).get("/api/scorecard").json()
    assert data["summary"]["total_picks"] == 1
    assert data["rows"][0]["symbol"] == "GOOD" and data["rows"][0]["ret_5d_pct"] is None
