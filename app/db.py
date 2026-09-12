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


def init_db() -> None:
    conn = get_connection()
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
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
