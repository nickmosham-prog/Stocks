"""Builds the historical "normal volume for this time of day" baseline and
daily OHLC/ATR14 used by the breakout scorer. Run once per day after close.
"""

from __future__ import annotations

import logging
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from app import db
from app.config import load_settings
from app.datasource.base import DataSource
from app.market_calendar import bucket_index, is_premarket_window, is_regular_window

log = logging.getLogger(__name__)


def _session_for_timestamp(ts: pd.Timestamp) -> str | None:
    if is_premarket_window(ts):
        return "premarket"
    if is_regular_window(ts):
        return "regular"
    return None


def build_profile_rows(df: pd.DataFrame, symbol: str, bucket_minutes: int) -> list[dict]:
    et = ZoneInfo(load_settings().get("app", "timezone", default="America/New_York"))
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    local_index = idx.tz_convert(et)

    # (date, session, bucket) -> summed volume for that day/bucket
    per_day_bucket: dict[tuple, float] = {}
    for ts, volume in zip(local_index, df["Volume"].fillna(0).tolist()):
        session = _session_for_timestamp(ts)
        if session is None:
            continue
        bucket = bucket_index(ts, session, bucket_minutes)
        key = (ts.date(), session, bucket)
        per_day_bucket[key] = per_day_bucket.get(key, 0.0) + float(volume)

    # group across days -> avg/stdev per (session, bucket)
    by_bucket: dict[tuple, list[float]] = {}
    for (_, session, bucket), volume in per_day_bucket.items():
        by_bucket.setdefault((session, bucket), []).append(volume)

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    rows = []
    for (session, bucket), volumes in by_bucket.items():
        avg_volume = statistics.mean(volumes)
        stdev_volume = statistics.pstdev(volumes) if len(volumes) > 1 else 0.0
        rows.append(
            {
                "symbol": symbol,
                "bucket_index": bucket,
                "session": session,
                "avg_volume": avg_volume,
                "stdev_volume": stdev_volume,
                "sample_days": len(volumes),
                "updated_at": now_iso,
            }
        )
    return rows


def build_daily_ohlc_rows(df: pd.DataFrame, symbol: str) -> list[dict]:
    if df.empty:
        return []
    df = df.sort_index()
    prev_close = df["Close"].shift(1)
    true_range = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = true_range.rolling(window=14, min_periods=5).mean()

    rows = []
    for ts, row in df.iterrows():
        trade_date = ts.strftime("%Y-%m-%d")
        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
                "high": float(row["High"]) if pd.notna(row["High"]) else None,
                "low": float(row["Low"]) if pd.notna(row["Low"]) else None,
                "close": float(row["Close"]) if pd.notna(row["Close"]) else None,
                "volume": float(row["Volume"]) if pd.notna(row["Volume"]) else None,
                "atr14": float(atr14.loc[ts]) if pd.notna(atr14.loc[ts]) else None,
            }
        )
    return rows


def _fetch_symbol_data(source: DataSource, symbol: str, lookback_days: int, bucket_minutes: int):
    profile_df = source.get_profile_history(symbol, days=lookback_days, interval=f"{bucket_minutes}m")
    daily_df = source.get_daily_history(symbol, days=30)
    profile_rows = build_profile_rows(profile_df, symbol, bucket_minutes) if profile_df is not None else []
    daily_rows = build_daily_ohlc_rows(daily_df, symbol) if daily_df is not None else []
    return symbol, profile_rows, daily_rows


def refresh_historical_data(source: DataSource, symbols: list[str]) -> tuple[int, int]:
    """Rebuilds historical_volume_profile and daily_ohlc for all symbols.

    Returns (succeeded_count, failed_count).
    """
    settings = load_settings()
    lookback_days = settings.get("volume_profile", "lookback_days", default=20)
    bucket_minutes = settings.get("volume_profile", "bucket_minutes", default=5)
    max_workers = settings.get("enrichment", "max_workers", default=5)

    succeeded, failed = 0, 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_symbol_data, source, symbol, lookback_days, bucket_minutes): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                _, profile_rows, daily_rows = future.result()
            except Exception:
                log.exception("failed to refresh historical data for %s", symbol)
                failed += 1
                continue
            if not profile_rows and not daily_rows:
                failed += 1
                continue
            for row in profile_rows:
                db.upsert("historical_volume_profile", ["symbol", "bucket_index", "session"], row)
            for row in daily_rows:
                db.upsert("daily_ohlc", ["symbol", "trade_date"], row)
            succeeded += 1
    return succeeded, failed


def has_fresh_profile(symbol: str) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "SELECT updated_at FROM historical_volume_profile WHERE symbol = ? ORDER BY updated_at DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
    if row is None:
        return False
    updated_at = datetime.fromisoformat(row["updated_at"])
    age_hours = (datetime.now(tz=timezone.utc) - updated_at).total_seconds() / 3600
    return age_hours < 20  # refreshed within the last trading day


def needs_bootstrap(symbols: list[str]) -> bool:
    """True if fewer than half the watchlist has a fresh volume profile,
    meaning the EOD refresh should run once synchronously before the
    scan loops start (so day one works without a manual step)."""
    if not symbols:
        return False
    fresh = sum(1 for symbol in symbols if has_fresh_profile(symbol))
    return fresh < len(symbols) / 2
