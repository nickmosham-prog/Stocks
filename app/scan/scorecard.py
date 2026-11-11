"""Scorecard: a permanent record of every Top Pick and what happened next.

Without this there is no way to tell whether the picks are any good. Each
pick is logged once per day (the first time it appears in the regular
session) with its entry price, and later filled in with:

- the stock's return after 1, 5 and 20 trading days, next to the S&P 500's
  (SPY) return over the same days
- whether the exit plan's stop or target was reached first
- the option idea's price at 5, 10 and 20 trading days

Stock results come from the daily bars already in the database. Option
results need a quote, so they are only updated while the market is open.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone

import numpy as np

from app import db
from app.datasource.base import DataSource
from app.market_calendar import is_regular_window, now_et
from app.scan.numeric import clean, is_valid

log = logging.getLogger(__name__)

BENCHMARK = "SPY"
HORIZONS = (1, 5, 20)             # trading days, for stock returns
OPTION_CHECKPOINTS = (5, 10, 20)  # trading days, for the option idea
TRACK_DAYS = 20                   # stop/target and option tracking window
MIN_RESULTS_FOR_TRACK_RECORD = 5
CHAIN_LOOKUP_PAUSE_SECONDS = 3.2  # moomoo allows ~10 option-chain requests per 30 seconds


# --- logging -------------------------------------------------------------------

def log_picks(records: list[dict], session: str, spy_price: float | None, market_regime: str | None) -> int:
    """Records today's new picks. Only the regular session counts (pre-market
    prices are too thin to be a fair entry), and a symbol is logged once per
    day - the first time it is picked. Returns how many were new."""
    if session != "regular" or not records:
        return 0
    added = 0
    with db.cursor() as cur:
        for r in records:
            cur.execute(
                """
                INSERT OR IGNORE INTO pick_log
                    (pick_date, symbol, first_scan_ts, rank, pick_score, sector, market_regime,
                     entry_price, spy_entry_price, stop_price, target_price,
                     contract_symbol, option_type, strike, expiration, option_entry_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["scan_ts"][:10], r["symbol"], r["scan_ts"], r["rank"], r["pick_score"], r.get("sector"),
                    market_regime, r["price"], clean(spy_price), r.get("stop_price"), r.get("target_price"),
                    r.get("contract_symbol"), r.get("option_type"), r.get("strike"), r.get("expiration"),
                    r.get("option_price"),
                ),
            )
            added += cur.rowcount
    return added


# --- stock outcomes (pure) -----------------------------------------------------

def _pct(later: float | None, entry: float | None) -> float | None:
    if not (is_valid(later) and is_valid(entry) and entry > 0):
        return None
    return clean((later / entry - 1.0) * 100.0)


def compute_stock_outcome(
    pick_date: str,
    entry_price: float | None,
    bars: list[dict],
    spy_entry_price: float | None = None,
    spy_bars: list[dict] | None = None,
    stop_price: float | None = None,
    target_price: float | None = None,
) -> dict:
    """`bars`: daily {trade_date, high, low, close} in date order. Returns
    the outcome columns for one pick. "N days" means the close N trading
    days after the pick date; anything not yet known is None."""
    after = [b for b in bars if b["trade_date"] > pick_date]
    spy_after = [b for b in (spy_bars or []) if b["trade_date"] > pick_date]
    on_or_after = [b for b in bars if b["trade_date"] >= pick_date and is_valid(b.get("close"))]

    result: dict = {
        "days_elapsed": len(after),
        "last_close": on_or_after[-1]["close"] if on_or_after else None,
        "outcome": None,
        "outcome_date": None,
    }
    result["ret_latest_pct"] = _pct(result["last_close"], entry_price)
    for n in HORIZONS:
        result[f"ret_{n}d_pct"] = _pct(after[n - 1].get("close"), entry_price) if len(after) >= n else None
        result[f"spy_ret_{n}d_pct"] = (
            _pct(spy_after[n - 1].get("close"), spy_entry_price) if len(spy_after) >= n else None
        )

    # Which came first, the stop or the target? Judged on daily highs/lows
    # from the day after the pick. A day that touches both counts as stopped
    # (the order within a day is unknown, so assume the worse one).
    if is_valid(stop_price) and is_valid(target_price):
        for bar in after[:TRACK_DAYS]:
            if is_valid(bar.get("low")) and bar["low"] <= stop_price:
                result["outcome"], result["outcome_date"] = "stopped", bar["trade_date"]
                break
            if is_valid(bar.get("high")) and bar["high"] >= target_price:
                result["outcome"], result["outcome_date"] = "target", bar["trade_date"]
                break
    return result


def _daily_bars(symbol: str, since: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT trade_date, high, low, close FROM daily_ohlc WHERE symbol = ? AND trade_date >= ? "
            "ORDER BY trade_date",
            (symbol, since),
        )
        return [dict(row) for row in cur.fetchall()]


def update_stock_outcomes() -> int:
    """Refreshes returns and stop/target results for every logged pick from
    the daily bars in the database. Returns how many picks were updated."""
    with db.cursor() as cur:
        cur.execute("SELECT * FROM pick_log WHERE days_elapsed IS NULL OR days_elapsed < ?", (max(HORIZONS),))
        open_rows = [dict(row) for row in cur.fetchall()]
    if not open_rows:
        return 0
    earliest = min(row["pick_date"] for row in open_rows)
    spy_bars = _daily_bars(BENCHMARK, earliest)
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for row in open_rows:
        outcome = compute_stock_outcome(
            row["pick_date"], row["entry_price"], _daily_bars(row["symbol"], row["pick_date"]),
            row["spy_entry_price"], spy_bars, row["stop_price"], row["target_price"],
        )
        if row.get("outcome"):  # once decided, a stop/target result never changes
            outcome["outcome"], outcome["outcome_date"] = row["outcome"], row["outcome_date"]
        assignments = ", ".join(f"{column} = :{column}" for column in outcome)
        with db.cursor() as cur:
            cur.execute(
                f"UPDATE pick_log SET {assignments}, updated_at = :updated_at WHERE id = :id",
                {**outcome, "updated_at": now_iso, "id": row["id"]},
            )
    return len(open_rows)


# --- option outcomes -----------------------------------------------------------

def _mark(contract: dict) -> float | None:
    """Midpoint of bid/ask when both are quoted, else the last trade."""
    bid, ask, last = contract.get("bid"), contract.get("ask"), contract.get("last_price")
    if is_valid(bid) and is_valid(ask) and bid > 0 and ask > 0:
        return clean((bid + ask) / 2.0)
    return last if is_valid(last) and last > 0 else None


def trading_days_since(pick_date: str, today: date) -> int:
    """Weekdays from the pick date to today (holidays are not excluded, so
    this can run a day ahead around a holiday)."""
    return int(np.busday_count(date.fromisoformat(pick_date), today))


def option_updates(row: dict, mark: float | None, today: date, now_iso: str) -> dict:
    """Column updates for one pick's option idea given its current mark."""
    if not is_valid(mark):
        return {}
    updates = {"option_last_price": mark, "option_last_checked": now_iso}
    elapsed = trading_days_since(row["pick_date"], today)
    for n in OPTION_CHECKPOINTS:
        column = f"option_ret_{n}d_pct"
        # Taken at the first update on or after day N, then never overwritten.
        if elapsed >= n and row.get(column) is None:
            updates[column] = _pct(mark, row["option_entry_price"])
    return updates


def _fetch_marks(source: DataSource, rows: list[dict], pause_seconds: float) -> dict[int, float]:
    """pick_log id -> current option mark."""
    marks: dict[int, float] = {}
    batch_quotes = getattr(source, "get_option_marks", None)
    remaining = rows
    if batch_quotes is not None:  # moomoo: every contract in one request
        coded = [r for r in rows if str(r["contract_symbol"]).startswith("US.")]
        quotes = batch_quotes([r["contract_symbol"] for r in coded]) if coded else {}
        for r in coded:
            if is_valid(quotes.get(r["contract_symbol"])):
                marks[r["id"]] = quotes[r["contract_symbol"]]
        remaining = [r for r in rows if r["id"] not in marks]

    groups: dict[tuple, list[dict]] = {}
    for r in remaining:
        groups.setdefault((r["symbol"], r["expiration"]), []).append(r)
    for index, ((symbol, expiration), group) in enumerate(groups.items()):
        if index and len(groups) > 8 and pause_seconds:
            time.sleep(pause_seconds)
        try:
            chain = source.get_option_chain(symbol, expirations=[expiration]) or []
        except Exception:
            log.exception("option quote lookup failed for %s %s", symbol, expiration)
            continue
        for r in group:
            for contract in chain:
                same_strike = is_valid(contract.get("strike")) and abs(contract["strike"] - r["strike"]) < 0.005
                if same_strike and contract.get("option_type") == r["option_type"]:
                    mark = _mark(contract)
                    if mark is not None:
                        marks[r["id"]] = mark
                    break
    return marks


def update_option_outcomes(
    source: DataSource, today: date | None = None, pause_seconds: float = CHAIN_LOOKUP_PAUSE_SECONDS
) -> int:
    """Refreshes the current price of each tracked option idea. Returns how
    many were updated."""
    today = today or now_et().date()
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM pick_log
            WHERE contract_symbol IS NOT NULL AND option_entry_price > 0 AND strike IS NOT NULL
              AND expiration >= ? AND option_ret_20d_pct IS NULL
            """,
            (today.isoformat(),),
        )
        rows = [dict(row) for row in cur.fetchall()]
    rows = [r for r in rows if trading_days_since(r["pick_date"], today) <= TRACK_DAYS + 2]
    if not rows:
        return 0
    marks = _fetch_marks(source, rows, pause_seconds)
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    updated = 0
    for r in rows:
        updates = option_updates(r, marks.get(r["id"]), today, now_iso)
        if not updates:
            continue
        assignments = ", ".join(f"{column} = :{column}" for column in updates)
        with db.cursor() as cur:
            cur.execute(f"UPDATE pick_log SET {assignments} WHERE id = :id", {**updates, "id": r["id"]})
        updated += 1
    return updated


def update_outcomes(source: DataSource | None = None) -> dict:
    """Stock results always; option prices only while the market is open
    (quotes outside the session are stale or empty)."""
    result = {"stocks": update_stock_outcomes(), "options": 0}
    if source is not None and is_regular_window():
        result["options"] = update_option_outcomes(source)
    return result


# --- reporting -----------------------------------------------------------------

def _average(values: list[float | None]) -> float | None:
    valid = [v for v in values if is_valid(v)]
    return clean(sum(valid) / len(valid)) if valid else None


def summarize(rows: list[dict]) -> dict:
    """Headline numbers for the Scorecard tab. Everything is judged at 5
    trading days (the middle of a swing trade), with 1- and 20-day averages
    for context."""
    done_5d = [r for r in rows if is_valid(r.get("ret_5d_pct"))]
    option_done = [r for r in rows if is_valid(r.get("option_ret_5d_pct"))]
    return {
        "total_picks": len(rows),
        "with_5d_result": len(done_5d),
        "win_rate_5d_pct": clean(sum(1 for r in done_5d if r["ret_5d_pct"] > 0) / len(done_5d) * 100.0)
        if done_5d else None,
        "avg_ret_1d_pct": _average([r.get("ret_1d_pct") for r in rows]),
        "avg_ret_5d_pct": _average([r["ret_5d_pct"] for r in done_5d]),
        "avg_ret_20d_pct": _average([r.get("ret_20d_pct") for r in rows]),
        "avg_spy_ret_5d_pct": _average([r.get("spy_ret_5d_pct") for r in done_5d]),
        "avg_excess_5d_pct": _average(
            [r["ret_5d_pct"] - r["spy_ret_5d_pct"] for r in done_5d if is_valid(r.get("spy_ret_5d_pct"))]
        ),
        "options_with_5d_result": len(option_done),
        "avg_option_ret_5d_pct": _average([r["option_ret_5d_pct"] for r in option_done]),
        "option_win_rate_5d_pct": clean(
            sum(1 for r in option_done if r["option_ret_5d_pct"] > 0) / len(option_done) * 100.0
        ) if option_done else None,
        "targets_hit": sum(1 for r in rows if r.get("outcome") == "target"),
        "stopped_out": sum(1 for r in rows if r.get("outcome") == "stopped"),
    }


def load_rows(limit: int = 500) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM pick_log ORDER BY pick_date DESC, rank LIMIT ?", (limit,))
        return [dict(row) for row in cur.fetchall()]


def track_record_line() -> str | None:
    """One sentence for the digest, once enough picks have 5-day results."""
    summary = summarize(load_rows(limit=5000))
    if summary["with_5d_result"] < MIN_RESULTS_FOR_TRACK_RECORD:
        return None
    line = (
        f"Track record so far: {summary['with_5d_result']} picks with 5-day results, "
        f"average {summary['avg_ret_5d_pct']:+.1f}%"
    )
    if summary["avg_spy_ret_5d_pct"] is not None:
        line += f" (S&P 500 {summary['avg_spy_ret_5d_pct']:+.1f}% over the same days)"
    return line + f", {summary['win_rate_5d_pct']:.0f}% were up. See the Scorecard tab."
