"""Fundamentals quality gate: "real financials, not just narratives."

Fundamentals change slowly (quarterly at most) so this is refreshed once a
day, not every 5-minute scan cycle - mirrors app/scan/volume_profile.py's
refresh_all/has_fresh_*/needs_bootstrap shape exactly.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from app import db
from app.config import load_settings
from app.datasource.base import DataSource
from app.scan.numeric import clean, is_valid

log = logging.getLogger(__name__)


def evaluate_fundamentals(fundamentals: dict | None, settings=None) -> dict:
    """Rolls a fundamentals snapshot up into a three-valued status:
    'pass' / 'fail' / 'unknown'. Any required check that can't be computed
    (missing field) makes the whole result 'unknown' - treated the same as
    'fail' for gating purposes (fail-closed: an unverifiable stock never
    gets a BUY badge just because data happened to be missing), but stored
    and shown distinctly so the UI can say "data unavailable" instead of a
    misleading "failed"."""
    settings = settings or load_settings()
    cfg = settings.get("scoring", "fundamentals", default={})

    if fundamentals is None:
        return _result(None, None, None, None, None, "unknown")

    net_income = fundamentals.get("net_income")
    pe = fundamentals.get("trailing_pe")
    growth = fundamentals.get("revenue_growth_yoy")

    is_profitable = (net_income > 0) if is_valid(net_income) else None
    pe_in_range = (
        cfg.get("min_pe", 0) <= pe <= cfg.get("max_pe", 60)
    ) if is_valid(pe) else None
    revenue_growing = (growth > 0) if is_valid(growth) else None

    checks = []
    if cfg.get("require_positive_earnings", True):
        checks.append(is_profitable)
    if cfg.get("require_pe_in_range", True):
        checks.append(pe_in_range)
    if cfg.get("require_revenue_growth", True):
        checks.append(revenue_growing)

    if any(c is None for c in checks):
        status = "unknown"
    elif checks and all(checks):
        status = "pass"
    else:
        status = "fail"

    return _result(is_profitable, clean(pe), pe_in_range, clean(growth), revenue_growing, status)


def _result(is_profitable, pe, pe_in_range, growth, revenue_growing, status) -> dict:
    return {
        "is_profitable": is_profitable,
        "trailing_pe": pe,
        "pe_in_range": pe_in_range,
        "revenue_growth_yoy": growth,
        "revenue_growing": revenue_growing,
        "fundamentals_status": status,
        "fundamentals_pass": status == "pass",
    }


def _fetch_and_evaluate(source: DataSource, symbol: str, settings) -> dict:
    fundamentals = source.get_fundamentals(symbol)
    evaluated = evaluate_fundamentals(fundamentals, settings)
    row = {
        "symbol": symbol,
        "net_income": clean((fundamentals or {}).get("net_income")),
        "trailing_eps": clean((fundamentals or {}).get("trailing_eps")),
        "trailing_pe": evaluated["trailing_pe"],
        "revenue_growth_yoy": evaluated["revenue_growth_yoy"],
        "is_profitable": _to_int_or_none(evaluated["is_profitable"]),
        "pe_in_range": _to_int_or_none(evaluated["pe_in_range"]),
        "revenue_growing": _to_int_or_none(evaluated["revenue_growing"]),
        "fundamentals_status": evaluated["fundamentals_status"],
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "profit_margin": clean((fundamentals or {}).get("profit_margin")),
        "target_mean_price": clean((fundamentals or {}).get("target_mean_price")),
        "recommendation_mean": clean((fundamentals or {}).get("recommendation_mean")),
        "analyst_count": clean((fundamentals or {}).get("analyst_count")),
        "next_earnings_date": (fundamentals or {}).get("next_earnings_date"),
    }
    return row


def _to_int_or_none(value: bool | None) -> int | None:
    return None if value is None else int(value)


def refresh_all(source: DataSource, symbols: list[str]) -> tuple[int, int]:
    """Rebuilds the `fundamentals` table for every symbol. Returns
    (succeeded_count, failed_count). A "failed" fetch still writes an
    'unknown'-status row (fetch just returned nothing usable), it's not a
    crash - only an exception counts as failed."""
    settings = load_settings()
    max_workers = settings.get("enrichment", "max_workers", default=5)

    succeeded, failed = 0, 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_and_evaluate, source, symbol, settings): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                row = future.result()
            except Exception:
                log.exception("failed to refresh fundamentals for %s", symbol)
                failed += 1
                continue
            db.upsert("fundamentals", ["symbol"], row)
            succeeded += 1
    return succeeded, failed


def _missing_picks_fields() -> bool:
    """Rows fetched before the Top Picks upgrade have every new column NULL;
    refetch rather than wait up to a day for the scheduled refresh."""
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN profit_margin IS NOT NULL OR target_mean_price IS NOT NULL
                            THEN 1 ELSE 0 END) AS enriched
            FROM fundamentals
            """
        )
        row = cur.fetchone()
    return bool(row["total"]) and not row["enriched"]


def has_fresh_fundamentals(symbol: str) -> bool:
    settings = load_settings()
    refresh_hours = settings.get("scoring", "fundamentals", "refresh_hours", default=24)
    with db.cursor() as cur:
        cur.execute("SELECT fetched_at FROM fundamentals WHERE symbol = ?", (symbol,))
        row = cur.fetchone()
    if row is None:
        return False
    fetched_at = datetime.fromisoformat(row["fetched_at"])
    age_hours = (datetime.now(tz=timezone.utc) - fetched_at).total_seconds() / 3600
    return age_hours < refresh_hours


def needs_bootstrap(symbols: list[str]) -> bool:
    """True if fewer than half the watchlist has fresh fundamentals, or the
    stored rows predate the Top Picks fields (analyst target etc.) -
    otherwise mirrors volume_profile.needs_bootstrap."""
    if symbols and _missing_picks_fields():
        return True
    if not symbols:
        return False
    fresh = sum(1 for symbol in symbols if has_fresh_fundamentals(symbol))
    return fresh < len(symbols) / 2
