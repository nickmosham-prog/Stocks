import math

import pandas as pd

from app.scan.trend import compute_trend


def _series(values):
    return pd.Series(values, dtype=float)


def test_steady_uptrend_metrics():
    closes = _series([100 + i for i in range(260)])  # 100 .. 359
    t = compute_trend(closes)
    assert t["last_close"] == 359
    assert t["sma50"] == sum(range(310, 360)) / 50
    assert t["sma200"] == sum(range(160, 360)) / 200
    assert t["high_52w"] == 359
    assert t["pct_from_high"] == 0
    assert math.isclose(t["ret_3m_pct"], (359 / 296 - 1) * 100)
    assert t["history_days"] == 260


def test_relative_strength_vs_spy():
    stock = _series([100.0] * 200 + [110.0] * 63)  # +10% over the last 63 sessions
    spy = _series([100.0] * 200 + [104.0] * 63)    # +4%
    t = compute_trend(stock, spy)
    assert math.isclose(t["rs_3m_pct"], 6.0)


def test_short_history_leaves_long_metrics_none():
    t = compute_trend(_series([50.0] * 30))
    assert t["sma50"] is None
    assert t["sma200"] is None
    assert t["high_52w"] is None
    assert t["ret_3m_pct"] is None
    assert t["rs_3m_pct"] is None
    assert t["last_close"] == 50.0


def test_nan_bars_are_ignored():
    values = [100.0] * 60
    values[10] = float("nan")
    t = compute_trend(_series(values))
    assert t["sma50"] == 100.0
    assert t["history_days"] == 59


def test_pct_from_high_below_peak():
    closes = _series([100.0] * 100 + [120.0] + [90.0] * 20)
    t = compute_trend(closes)
    assert math.isclose(t["pct_from_high"], (90 / 120 - 1) * 100)


def test_empty_series():
    t = compute_trend(_series([]))
    assert t["history_days"] == 0
    assert t["last_close"] is None
