"""Orchestrates one full scan cycle: bulk RVOL+breakout pass over the whole
watchlist, then options/news enrichment for the top-N ranked tickers, then
persists everything and rolls up an alpha score per ticker.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from app import alerts, db
from app.config import load_settings, load_watchlist
from app.datasource.base import DataSource
from app.datasource.cache import TTLCache
from app.market_calendar import bucket_index, now_et
from app.scan import alpha_score, breakout, buy_setup, news, options_flow, rvol

log = logging.getLogger(__name__)

_news_cache: TTLCache | None = None
_options_cache: TTLCache | None = None


def _caches() -> tuple[TTLCache, TTLCache]:
    global _news_cache, _options_cache
    if _news_cache is None:
        settings = load_settings()
        _news_cache = TTLCache(settings.get("enrichment", "news_cache_minutes", default=30) * 60)
        _options_cache = TTLCache(settings.get("enrichment", "options_cache_minutes", default=15) * 60)
    return _news_cache, _options_cache


def _filter_session_bars(bars: pd.DataFrame, session: str) -> pd.DataFrame:
    settings = load_settings()
    tz = ZoneInfo(settings.get("app", "timezone", default="America/New_York"))
    idx = bars.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    local_index = idx.tz_convert(tz)
    today = now_et().date()

    from app.market_calendar import session_window

    start, end = session_window(session)
    mask = [
        (ts.date() == today) and (start <= ts.timetz().replace(tzinfo=None) < end)
        for ts in local_index
    ]
    filtered = bars.loc[mask]
    filtered.index = local_index[mask]
    return filtered


def _score_symbol(symbol: str, bars: pd.DataFrame, session: str, bucket_minutes: int) -> dict | None:
    session_bars = _filter_session_bars(bars, session)
    if session_bars.empty:
        return None
    price = float(session_bars["Close"].dropna().iloc[-1])
    # .max()/.min() skip NaN individual bars fine, but return NaN outright
    # if EVERY bar in the session is missing High/Low (seen on thin/illiquid
    # names) - fall back to the known-good close rather than let a NaN
    # leak into the breakout math downstream.
    high_series = session_bars["High"].dropna()
    low_series = session_bars["Low"].dropna()
    today_high = float(high_series.max()) if not high_series.empty else price
    today_low = float(low_series.min()) if not low_series.empty else price
    cum_volume_today = float(session_bars["Volume"].fillna(0).sum())

    current_bucket = bucket_index(now_et(), session, bucket_minutes)
    cum_avg = rvol.cumulative_avg_volume(symbol, session, current_bucket)
    rv = rvol.compute_rvol(cum_volume_today, cum_avg)
    rv_score = rvol.compute_rvol_score(rv)

    prior = breakout.get_prior_day_ohlc(symbol)
    if prior:
        b = breakout.compute_breakout(price, today_high, today_low, prior)
    else:
        b = {
            "gap_pct": None,
            "range_expansion": None,
            "breakout_level_pct": None,
            "breakout_score": None,
            "breakout_direction": None,
            "level_broken": False,
        }

    prelim = alpha_score.preliminary_score(rv_score, b["breakout_score"])

    return {
        "symbol": symbol,
        "price": price,
        "cum_volume_today": cum_volume_today,
        "cum_avg_volume": cum_avg,
        "rvol": rv,
        "rvol_score": rv_score,
        "prelim_score": prelim,
        **b,
    }


def _enrich_symbol(source: DataSource, symbol: str) -> dict:
    news_cache, options_cache = _caches()
    contracts = options_cache.get_or_set(symbol, lambda: source.get_option_chain(symbol))
    options_result = options_flow.compute_options_activity(contracts)
    has_recent_news = news_cache.get_or_set(symbol, lambda: news.fetch_and_store_news(source, symbol))
    options_result["has_recent_news"] = has_recent_news
    return options_result


def run_scan(source: DataSource, session: str) -> dict:
    """Runs one scan cycle for `session` ('premarket' or 'regular'). Returns a
    small summary dict (also recorded in scan_runs)."""
    settings = load_settings()
    symbols = load_watchlist()
    bucket_minutes = settings.get("volume_profile", "bucket_minutes", default=5)
    scan_ts = now_et().isoformat()
    started_at = datetime.now(tz=timezone.utc).isoformat()

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO scan_runs (run_type, started_at) VALUES (?, ?)",
            ("premarket" if session == "premarket" else "intraday", started_at),
        )
        run_id = cur.lastrowid

    bulk_bars = source.get_bulk_intraday_bars(
        symbols, period="1d", interval=f"{bucket_minutes}m", prepost=True
    )

    prelim: dict[str, dict] = {}
    failed = []
    for symbol in symbols:
        bars = bulk_bars.get(symbol)
        if bars is None or bars.empty:
            failed.append(symbol)
            continue
        try:
            result = _score_symbol(symbol, bars, session, bucket_minutes)
        except Exception:
            log.exception("scoring failed for %s", symbol)
            result = None
        if result is None:
            failed.append(symbol)
            continue
        prelim[symbol] = result

    ranked = sorted(
        prelim.items(), key=lambda kv: (kv[1]["prelim_score"] if kv[1]["prelim_score"] is not None else -1),
        reverse=True,
    )
    top_n = settings.get("enrichment", "top_n", default=30)
    max_workers = settings.get("enrichment", "max_workers", default=5)
    enrich_symbols = [symbol for symbol, _ in ranked[:top_n]]

    enrichment: dict[str, dict] = {}
    if enrich_symbols:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_enrich_symbol, source, symbol): symbol for symbol in enrich_symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    enrichment[symbol] = future.result()
                except Exception:
                    log.exception("enrichment failed for %s", symbol)

    hold_threshold = settings.get("scoring", "breakout", "hold_confirm_minutes", default=15)
    with db.cursor() as cur:
        cur.execute("SELECT symbol, breakout_direction, breakout_holding_since FROM latest_snapshot")
        prev_state = {row["symbol"]: dict(row) for row in cur.fetchall()}

    all_rows = []
    for symbol, data in prelim.items():
        enrich = enrichment.get(symbol)
        options_score = enrich["options_score"] if enrich else None
        call_put_ratio = enrich["call_put_ratio"] if enrich else None
        max_vol_oi_ratio = enrich["max_vol_oi_ratio"] if enrich else None
        avg_implied_volatility = enrich["avg_implied_volatility"] if enrich else None
        has_recent_news = bool(enrich["has_recent_news"]) if enrich else False

        final_score = alpha_score.compute_alpha_score(data["rvol_score"], data["breakout_score"], options_score)

        prev = prev_state.get(symbol, {})
        hold_state = breakout.update_hold_state(
            level_broken=data["level_broken"],
            direction=data["breakout_direction"],
            now=now_et(),
            prev_holding_since=prev.get("breakout_holding_since"),
            prev_direction=prev.get("breakout_direction"),
        )
        hold_minutes = hold_state["hold_minutes"]
        breakout_confirmed = bool(hold_minutes is not None and hold_minutes >= hold_threshold)

        row = {
            "symbol": symbol,
            "session": session,
            "scan_ts": scan_ts,
            "price": data["price"],
            "cum_volume_today": data["cum_volume_today"],
            "cum_avg_volume": data["cum_avg_volume"],
            "rvol": data["rvol"],
            "rvol_score": data["rvol_score"],
            "gap_pct": data["gap_pct"],
            "range_expansion": data["range_expansion"],
            "breakout_level_pct": data["breakout_level_pct"],
            "breakout_score": data["breakout_score"],
            "breakout_direction": data["breakout_direction"],
            "breakout_holding_since": hold_state["holding_since"],
            "breakout_hold_minutes": hold_minutes,
            "breakout_confirmed": int(breakout_confirmed),
            "options_score": options_score,
            "call_put_ratio": call_put_ratio,
            "max_vol_oi_ratio": max_vol_oi_ratio,
            "avg_implied_volatility": avg_implied_volatility,
            "has_recent_news": int(has_recent_news),
            "alpha_score": final_score,
            "data_stale": 0,
        }
        row["buy_signal"] = int(buy_setup.meets_buy_setup_criteria(row, settings))

        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scan_snapshots
                    (symbol, session, scan_ts, price, cum_volume_today, cum_avg_volume,
                     rvol, rvol_score, gap_pct, range_expansion, breakout_level_pct,
                     breakout_score, breakout_direction, breakout_holding_since,
                     breakout_hold_minutes, breakout_confirmed, options_score, call_put_ratio,
                     max_vol_oi_ratio, avg_implied_volatility, has_recent_news, alpha_score,
                     buy_signal, data_stale)
                VALUES
                    (:symbol, :session, :scan_ts, :price, :cum_volume_today, :cum_avg_volume,
                     :rvol, :rvol_score, :gap_pct, :range_expansion, :breakout_level_pct,
                     :breakout_score, :breakout_direction, :breakout_holding_since,
                     :breakout_hold_minutes, :breakout_confirmed, :options_score, :call_put_ratio,
                     :max_vol_oi_ratio, :avg_implied_volatility, :has_recent_news, :alpha_score,
                     :buy_signal, :data_stale)
                """,
                row,
            )
        db.upsert("latest_snapshot", ["symbol"], {**row, "enriched": int(enrich is not None)})
        all_rows.append(row)

        if enrich and enrich.get("flagged_contracts"):
            for c in enrich["flagged_contracts"]:
                with db.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO options_activity
                            (symbol, scan_ts, expiration, contract_symbol, option_type,
                             strike, volume, open_interest, vol_oi_ratio, last_price,
                             implied_volatility)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol,
                            scan_ts,
                            c["expiration"],
                            c["contract_symbol"],
                            c["option_type"],
                            c["strike"],
                            c["volume"],
                            c["open_interest"],
                            c["vol_oi_ratio"],
                            c["last_price"],
                            c.get("implied_volatility"),
                        ),
                    )

    try:
        alerts.process_alerts(all_rows)
    except Exception:
        log.exception("alert processing failed")

    finished_at = datetime.now(tz=timezone.utc).isoformat()
    error_summary = f"{len(failed)} symbols failed: {', '.join(failed[:20])}" if failed else None
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE scan_runs
            SET finished_at = ?, tickers_attempted = ?, tickers_failed = ?, error_summary = ?
            WHERE id = ?
            """,
            (finished_at, len(symbols), len(failed), error_summary, run_id),
        )

    log.info(
        "scan complete (session=%s): %d scored, %d failed, %d enriched",
        session, len(prelim), len(failed), len(enrichment),
    )
    return {"scored": len(prelim), "failed": len(failed), "enriched": len(enrichment)}
