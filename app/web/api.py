"""JSON API for the dashboard frontend. Reads only - all writes happen in
app/scan/runner.py during a scan cycle."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app import db
from app.config import load_watchlist
from app.market_calendar import current_session, is_market_day, now_et

router = APIRouter(prefix="/api")

SORTABLE_COLUMNS = {
    "alpha_score",
    "rvol_score",
    "rvol",
    "breakout_score",
    "options_score",
    "gap_pct",
    "cum_volume_today",
    "price",
    "buy_signal",
}


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


@router.get("/rankings")
def get_rankings(
    session: str = Query("all", pattern="^(all|premarket|regular)$"),
    sort: str = Query("alpha_score"),
    limit: int = Query(100, ge=1, le=500),
):
    if sort not in SORTABLE_COLUMNS:
        sort = "alpha_score"
    where = "" if session == "all" else "WHERE session = ?"
    params: tuple = () if session == "all" else (session,)
    with db.cursor() as cur:
        cur.execute(
            f"""
            SELECT * FROM latest_snapshot
            {where}
            ORDER BY {sort} IS NULL, {sort} DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = cur.fetchall()
    return {"rows": _rows_to_dicts(rows)}


@router.get("/ticker/{symbol}")
def get_ticker(symbol: str):
    symbol = symbol.upper()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM latest_snapshot WHERE symbol = ?", (symbol,))
        latest = cur.fetchone()
        if latest is None:
            raise HTTPException(status_code=404, detail=f"no data for {symbol} yet")

        cur.execute(
            "SELECT * FROM scan_snapshots WHERE symbol = ? ORDER BY scan_ts DESC LIMIT 50",
            (symbol,),
        )
        history = cur.fetchall()

        cur.execute(
            "SELECT * FROM news_items WHERE symbol = ? ORDER BY published_at DESC LIMIT 20",
            (symbol,),
        )
        news_rows = cur.fetchall()

        cur.execute(
            """
            SELECT * FROM options_activity
            WHERE symbol = ? AND scan_ts = (SELECT MAX(scan_ts) FROM options_activity WHERE symbol = ?)
            ORDER BY vol_oi_ratio DESC
            """,
            (symbol, symbol),
        )
        options_rows = cur.fetchall()

    return {
        "latest": dict(latest),
        "history": _rows_to_dicts(history),
        "news": _rows_to_dicts(news_rows),
        "options": _rows_to_dicts(options_rows),
    }


@router.get("/news")
def get_news(symbol: str | None = None, limit: int = Query(50, ge=1, le=200)):
    if symbol:
        query = "SELECT * FROM news_items WHERE symbol = ? ORDER BY published_at DESC LIMIT ?"
        params = (symbol.upper(), limit)
    else:
        query = "SELECT * FROM news_items ORDER BY published_at DESC LIMIT ?"
        params = (limit,)
    with db.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return {"rows": _rows_to_dicts(rows)}


@router.get("/options")
def get_all_options(limit: int = Query(50, ge=1, le=200)):
    """Most recent flagged options activity across the whole watchlist,
    one snapshot per symbol (its latest scan), ranked by vol/OI ratio."""
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT oa.* FROM options_activity oa
            INNER JOIN (
                SELECT symbol, MAX(scan_ts) AS max_ts FROM options_activity GROUP BY symbol
            ) latest ON oa.symbol = latest.symbol AND oa.scan_ts = latest.max_ts
            ORDER BY oa.vol_oi_ratio DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return {"rows": _rows_to_dicts(rows)}


@router.get("/options/{symbol}")
def get_options(symbol: str):
    symbol = symbol.upper()
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM options_activity
            WHERE symbol = ? AND scan_ts = (SELECT MAX(scan_ts) FROM options_activity WHERE symbol = ?)
            ORDER BY vol_oi_ratio DESC
            """,
            (symbol, symbol),
        )
        rows = cur.fetchall()
    return {"rows": _rows_to_dicts(rows)}


@router.get("/status")
def get_status():
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT run_type, started_at, finished_at, tickers_attempted, tickers_failed, error_summary
            FROM scan_runs
            WHERE id IN (
                SELECT MAX(id) FROM scan_runs GROUP BY run_type
            )
            ORDER BY started_at DESC
            """
        )
        runs = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM latest_snapshot")
        tracked = cur.fetchone()["n"]

    return {
        "market_open": is_market_day() and current_session() is not None,
        "current_session": current_session(),
        "server_time_et": now_et().isoformat(),
        "tickers_tracked": tracked,
        "recent_runs": _rows_to_dicts(runs),
    }


@router.get("/watchlist")
def get_watchlist():
    return {"symbols": load_watchlist()}
