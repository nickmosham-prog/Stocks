# Stock Volume & Alpha Dashboard

Tracks stocks showing unusual pre-market and intraday trading volume, and
scores them for "smart money" activity using free, no-API-key data
(Yahoo Finance via `yfinance`). Runs automatically Monday-Friday during
market hours as a single always-on process with a web dashboard.

## What it does

Every few minutes during pre-market (7:00-9:30am ET) and the regular
session (9:30am-4:00pm ET), it:

1. Pulls recent price/volume bars for your watchlist.
2. Computes **relative volume (RVOL)** - today's volume so far vs. the
   normal volume for this time of day, based on a 20-day history.
3. Computes a **breakout score** from gap %, range expansion vs. ATR, and
   breaks above/below the prior day's high/low.
4. For the most interesting tickers each cycle, pulls **options chain**
   data (volume/open-interest ratio + call/put skew) as a "smart money"
   proxy, and recent **news headlines** tagged by catalyst type (earnings,
   FDA, upgrade/downgrade, M&A, etc.).
5. Combines RVOL + breakout + options into a single ranked **Alpha Score**
   per ticker.
6. Emails you when a ticker's Alpha Score crosses a threshold (see
   **Email alerts** below) - one summary email per cycle, not one per
   ticker, with a per-symbol cooldown so a hot name doesn't spam you.

Every night after the close, it rebuilds the historical volume baseline
used for RVOL and refreshes prior-day OHLC/ATR for the breakout scorer.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Then open **http://127.0.0.1:8000** in your browser. Leave the process
running (e.g. in a terminal tab, `screen`/`tmux` session, or as a
background service) and it will keep scanning Monday-Friday during market
hours on its own - no manual steps needed day to day.

On first launch it will spend a few minutes building the historical
volume baseline before the first scan, since nothing has been fetched yet.

## Configuring

- **`config/settings.yaml`** - scan intervals, market hours, scoring
  weights/caps, batch size, how many tickers get options/news enrichment
  each cycle, and news keyword tags. Well-commented; edit and restart to
  apply.
- **`config/watchlist.yaml`** - which tickers to track. Ships with ~150
  liquid/volatile US names across sectors. Add or remove symbols (one per
  line) and restart.

## Email alerts

Alerts are off by default until you configure real credentials (the shipped
`app_password: "CHANGE_ME"` deliberately disables sending). To turn them on:

1. Create a **Gmail App Password**: Google Account -> Security -> 2-Step
   Verification -> App passwords. (Requires 2-Step Verification to be
   enabled on the account.) This is a 16-character password scoped to one
   app - not your real Gmail password, and you can revoke it any time.
2. Edit `config/settings.yaml`'s `alerts:` section:
   ```yaml
   alerts:
     enabled: true
     alpha_score_threshold: 80     # 0-100, lower = more (noisier) alerts
     cooldown_minutes: 60          # min gap between alerts for the same ticker
     email:
       smtp_host: "smtp.gmail.com"
       smtp_port: 587
       from_address: "you@gmail.com"
       app_password: "xxxx xxxx xxxx xxxx"   # the App Password from step 1
       to_address: "you@gmail.com"           # can be a different address
   ```
3. Restart `python main.py`.

Using a non-Gmail provider works too - just change `smtp_host`/`smtp_port`
to that provider's SMTP settings; `app_password` becomes whatever
credential that provider uses for SMTP login.

**Note:** this alert is delivered by email only, from wherever `main.py` is
running. There's no push notification, SMS, or "live" dashboard hosted
outside your machine - the web dashboard at `http://127.0.0.1:8000` and
this email are the only two outputs.

## Dashboard

- **Top Alpha** - all tracked tickers ranked by Alpha Score.
- **Pre-Market** / **Intraday** - ranked by session.
- **Options Flow** - tickers with unusual options volume/open-interest.
- **News** - recent tagged headlines for enriched tickers.

Click any row to open a drill-down panel with the full score breakdown,
recent headlines, and flagged option contracts. The dashboard polls for
new data every 30 seconds.

## Known limitations

- **`yfinance` is unofficial.** It scrapes/wraps Yahoo Finance endpoints
  with no SLA - it can be rate-limited, break, or change shape without
  notice. If scans start failing, check `logs/stocks.log` first.
- **Data is free and delayed**, not real-time. Pre/post-market quotes can
  be thin or stale for less-liquid names.
- **No market-holiday calendar.** The scheduler only checks Monday-Friday,
  so it will still attempt (and mostly no-op or log warnings on) market
  holidays and early closes. See `app/market_calendar.py` if you want to
  add one (e.g. via `pandas_market_calendars`).
- **Options data is a delayed volume/open-interest proxy**, not a real
  options-flow or sweep feed. Many small/mid-caps have no listed options
  and simply show no options score.
- **News tagging is keyword-based**, not NLP - expect occasional
  mis-tags or missed nuance.
- **Watchlist is curated, not the whole market.** A genuinely unusual
  mover outside your watchlist won't be caught. Keep it to a few hundred
  names to avoid Yahoo rate-limiting.
- **Single process, no built-in high availability.** If it crashes,
  restart it; scanning resumes and the volume-profile bootstrap check
  avoids redundant work.
- This tool is for research/screening only - not financial advice, and
  not a substitute for your own due diligence.

## Running tests

```bash
pip install -r requirements.txt   # includes pytest
pytest
```

Unit tests cover the scoring math (RVOL, breakout, alpha-score weight
renormalization) and market-hours/session-window logic. They don't hit
the network.

## Project layout

```
main.py                  # entry point: starts scheduler + web server
config/                  # settings.yaml, watchlist.yaml
app/
  config.py, db.py, schema.sql, market_calendar.py
  scheduler.py            # pre-market/intraday/EOD jobs
  datasource/             # yfinance calls, isolated behind an interface
  scan/                   # RVOL, breakout, options, news, alpha score
  web/                    # FastAPI API + static dashboard
tests/                    # pytest unit tests
```

The data-source layer (`app/datasource/`) is isolated behind an interface
(`DataSource` in `base.py`) so swapping in a paid real-time feed later
(Polygon, Finnhub, IEX, ...) only means writing a new implementation of
that interface - nothing in `app/scan` or `app/web` needs to change.
