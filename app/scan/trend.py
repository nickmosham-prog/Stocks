"""Longer-term trend context for Top Picks: moving averages, distance from
the 52-week high, and 3-month strength relative to the S&P 500 (SPY).

Computed once a day from the daily_ohlc table (no network), after the EOD
refresh and at startup - trend doesn't change meaningfully intraday.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from app import db
from app.scan.numeric import clean

log = logging.getLogger(__name__)

BENCHMARK = "SPY"
TRADING_DAYS_3M = 63
TRADING_DAYS_52W = 252


def _return_pct(closes: pd.Series, lookback: int) -> float | None:
    if len(closes) <= lookback:
        return None
    start = closes.iloc[-lookback - 1]
    if not start:
        return None
    return clean(float((closes.iloc[-1] / start - 1.0) * 100.0))


def compute_trend(closes: pd.Series, spy_closes: pd.Series | None = None) -> dict:
    """`closes`: daily closes in date order. Any metric without enough
    history is None rather than a guess."""
    closes = pd.to_numeric(closes, errors="coerce").dropna()
    n = len(closes)
    if n == 0:
        return {
            "last_close": None, "sma50": None, "sma200": None, "high_52w": None,
            "pct_from_high": None, "ret_3m_pct": None, "rs_3m_pct": None, "history_days": 0,
        }

    last = clean(float(closes.iloc[-1]))
    sma50 = clean(float(closes.iloc[-50:].mean())) if n >= 50 else None
    sma200 = clean(float(closes.iloc[-200:].mean())) if n >= 200 else None
    high_52w = clean(float(closes.iloc[-TRADING_DAYS_52W:].max())) if n >= 50 else None
    pct_from_high = clean((last / high_52w - 1.0) * 100.0) if last and high_52w else None

    ret_3m = _return_pct(closes, TRADING_DAYS_3M)
    rs_3m = None
    if ret_3m is not None and spy_closes is not None:
        spy_ret = _return_pct(pd.to_numeric(spy_closes, errors="coerce").dropna(), TRADING_DAYS_3M)
        if spy_ret is not None:
            rs_3m = clean(ret_3m - spy_ret)

    return {
        "last_close": last,
        "sma50": sma50,
        "sma200": sma200,
        "high_52w": high_52w,
        "pct_from_high": pct_from_high,
        "ret_3m_pct": ret_3m,
        "rs_3m_pct": rs_3m,
        "history_days": n,
    }


def _load_closes(symbol: str) -> pd.Series:
    with db.cursor() as cur:
        cur.execute(
            "SELECT trade_date, close FROM daily_ohlc WHERE symbol = ? ORDER BY trade_date",
            (symbol,),
        )
        rows = cur.fetchall()
    return pd.Series([row["close"] for row in rows], index=[row["trade_date"] for row in rows], dtype=float)


def refresh_all(symbols: list[str]) -> int:
    """Recomputes trend_metrics for every symbol from daily_ohlc. Returns
    how many symbols had any history. Cheap (DB-only), so it simply runs
    at startup and after every EOD refresh."""
    spy = _load_closes(BENCHMARK)
    spy = spy if not spy.empty else None
    computed_at = datetime.now(tz=timezone.utc).isoformat()
    updated = 0
    for symbol in symbols:
        try:
            metrics = compute_trend(_load_closes(symbol), spy)
        except Exception:
            log.exception("trend computation failed for %s", symbol)
            continue
        if not metrics["history_days"]:
            continue
        db.upsert("trend_metrics", ["symbol"], {"symbol": symbol, **metrics, "computed_at": computed_at})
        updated += 1
    return updated
