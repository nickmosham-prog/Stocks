"""Thin sqlite3 helper: connection factory + schema bootstrap + upsert helpers."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app.config import load_settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

_local = threading.local()


def _connect() -> sqlite3.Connection:
    settings = load_settings()
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def get_connection() -> sqlite3.Connection:
    """Returns a thread-local connection (sqlite3 connections aren't thread-safe)."""
    if not hasattr(_local, "conn"):
        _local.conn = _connect()
    return _local.conn


@contextmanager
def cursor():
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# Columns added to tables after their initial release. schema.sql's
# CREATE TABLE IF NOT EXISTS is a no-op on a database that already has the
# table, so anyone with an existing stocks.db needs these added by hand
# (idempotently) rather than losing their collected history to a rebuild.
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "scan_snapshots": {
        "breakout_holding_since": "TEXT",
        "breakout_hold_minutes": "REAL",
        "breakout_confirmed": "INTEGER NOT NULL DEFAULT 0",
        "avg_implied_volatility": "REAL",
        "buy_signal": "INTEGER NOT NULL DEFAULT 0",
    },
    "latest_snapshot": {
        "breakout_holding_since": "TEXT",
        "breakout_hold_minutes": "REAL",
        "breakout_confirmed": "INTEGER NOT NULL DEFAULT 0",
        "avg_implied_volatility": "REAL",
        "buy_signal": "INTEGER NOT NULL DEFAULT 0",
    },
    "options_activity": {
        "implied_volatility": "REAL",
    },
    "alert_log": {
        "kind": "TEXT NOT NULL DEFAULT 'general'",
    },
}


def _run_column_migrations() -> None:
    conn = get_connection()
    for table, columns in _COLUMN_MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def init_db() -> None:
    """Creates tables, migrates existing ones to the current column set, then
    creates indexes - in that order. Indexes must come last: on an existing
    database, an index on a newly-added column (e.g. alert_log.kind) would
    fail with "no such column" if created before the column-migration step
    that adds it. schema.sql has no semicolons inside string literals or
    comments, so this naive split on ";" is safe for this file specifically.
    """
    conn = get_connection()
    with open(SCHEMA_PATH, "r") as f:
        schema_sql = f.read()

    table_statements, index_statements = [], []
    for statement in schema_sql.split(";"):
        stripped = statement.strip()
        if not stripped:
            continue
        target = index_statements if stripped.upper().startswith("CREATE INDEX") else table_statements
        target.append(stripped + ";")

    conn.executescript("\n".join(table_statements))
    conn.commit()
    _run_column_migrations()
    conn.executescript("\n".join(index_statements))
    conn.commit()


def upsert(table: str, key_columns: list[str], row: dict) -> None:
    """Generic upsert (INSERT ... ON CONFLICT DO UPDATE) for a single row dict."""
    columns = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    col_list = ", ".join(columns)
    update_cols = [c for c in columns if c not in key_columns]
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
    conflict_cols = ", ".join(key_columns)
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict_cols}) DO UPDATE SET {update_clause}"
    )
    with cursor() as cur:
        cur.execute(sql, row)
