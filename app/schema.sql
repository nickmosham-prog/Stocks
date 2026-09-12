-- Stock Volume & Alpha Dashboard schema (SQLite)

CREATE TABLE IF NOT EXISTS tickers (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    sector TEXT,
    added_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

-- Historical average volume per (symbol, time-of-day bucket, session).
-- Rebuilt daily by the EOD refresh job from N days of intraday history.
CREATE TABLE IF NOT EXISTS historical_volume_profile (
    symbol TEXT NOT NULL,
    bucket_index INTEGER NOT NULL,
    session TEXT NOT NULL CHECK (session IN ('premarket', 'regular')),
    avg_volume REAL NOT NULL,
    stdev_volume REAL,
    sample_days INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, bucket_index, session)
);

-- Prior daily OHLCV + ATR14, used by the breakout scorer.
CREATE TABLE IF NOT EXISTS daily_ohlc (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    atr14 REAL,
    PRIMARY KEY (symbol, trade_date)
);

-- Full history of every scan cycle's results (append-only).
CREATE TABLE IF NOT EXISTS scan_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    session TEXT NOT NULL CHECK (session IN ('premarket', 'regular')),
    scan_ts TEXT NOT NULL,
    price REAL,
    cum_volume_today REAL,
    cum_avg_volume REAL,
    rvol REAL,
    rvol_score REAL,
    gap_pct REAL,
    range_expansion REAL,
    breakout_level_pct REAL,
    breakout_score REAL,
    breakout_direction TEXT,
    options_score REAL,
    call_put_ratio REAL,
    max_vol_oi_ratio REAL,
    has_recent_news INTEGER NOT NULL DEFAULT 0,
    alpha_score REAL,
    data_stale INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scan_snapshots_symbol_ts ON scan_snapshots(symbol, scan_ts);
CREATE INDEX IF NOT EXISTS idx_scan_snapshots_ts ON scan_snapshots(scan_ts);

-- Latest snapshot per symbol, upserted every cycle for fast dashboard reads.
CREATE TABLE IF NOT EXISTS latest_snapshot (
    symbol TEXT PRIMARY KEY,
    session TEXT NOT NULL CHECK (session IN ('premarket', 'regular')),
    scan_ts TEXT NOT NULL,
    price REAL,
    cum_volume_today REAL,
    cum_avg_volume REAL,
    rvol REAL,
    rvol_score REAL,
    gap_pct REAL,
    range_expansion REAL,
    breakout_level_pct REAL,
    breakout_score REAL,
    breakout_direction TEXT,
    options_score REAL,
    call_put_ratio REAL,
    max_vol_oi_ratio REAL,
    has_recent_news INTEGER NOT NULL DEFAULT 0,
    alpha_score REAL,
    data_stale INTEGER NOT NULL DEFAULT 0,
    enriched INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS options_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    scan_ts TEXT NOT NULL,
    expiration TEXT NOT NULL,
    contract_symbol TEXT NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
    strike REAL,
    volume REAL,
    open_interest REAL,
    vol_oi_ratio REAL,
    last_price REAL
);
CREATE INDEX IF NOT EXISTS idx_options_activity_symbol_ts ON options_activity(symbol, scan_ts);

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    headline TEXT NOT NULL,
    publisher TEXT,
    link TEXT UNIQUE,
    published_at TEXT,
    tags TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_items_symbol_published ON news_items(symbol, published_at DESC);

-- One row per email alert actually sent for a symbol, used to enforce the
-- per-symbol cooldown so the same ticker doesn't re-alert every cycle.
CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    alpha_score REAL,
    sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_log_symbol_sent ON alert_log(symbol, sent_at DESC);

-- Operational log: one row per scan/refresh run, surfaced in the UI status widget.
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL CHECK (run_type IN ('premarket', 'intraday', 'eod_refresh')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    tickers_attempted INTEGER,
    tickers_failed INTEGER,
    error_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_scan_runs_started ON scan_runs(started_at DESC);
