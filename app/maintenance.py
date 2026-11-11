"""Housekeeping: keeps the database from growing without bound."""

from __future__ import annotations

import logging
from datetime import timedelta

from app import db
from app.config import load_settings
from app.market_calendar import now_et

log = logging.getLogger(__name__)

# (table, timestamp column). Every one holds per-scan history that the
# dashboard only reads the latest of. Deliberately absent: pick_log (the
# Scorecard's permanent record), daily_ohlc, fundamentals, latest_snapshot.
PRUNED_TABLES = (
    ("scan_snapshots", "scan_ts"),
    ("options_activity", "scan_ts"),
    ("options_trade_recommendations", "scan_ts"),
    ("top_picks", "scan_ts"),
    ("pick_runs", "scan_ts"),
    ("news_items", "fetched_at"),
    ("scan_runs", "started_at"),
)


def prune_old_rows(days: int | None = None) -> int:
    """Deletes scan history older than `retention.days`. Returns rows removed."""
    if days is None:
        days = load_settings().get("retention", "days", default=30)
    if not days or days <= 0:
        return 0
    # ISO timestamps sort as text, and a bare date sorts before any time on
    # that date, so this works whatever UTC offset each column was stored with.
    cutoff = (now_et() - timedelta(days=days)).strftime("%Y-%m-%d")
    removed = 0
    with db.cursor() as cur:
        for table, column in PRUNED_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
            removed += cur.rowcount
    if removed:
        log.info("removed %d rows of scan history older than %d days", removed, days)
    return removed
