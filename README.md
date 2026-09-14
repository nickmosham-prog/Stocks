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
   breaks above/below the prior day's high/low - and tracks how many
   minutes a level break has *held* across consecutive scans, marking it
   "confirmed" once it clears `hold_confirm_minutes` (default 15). A level
   that just triggered once is a weaker signal than one that's held.
4. For the most interesting tickers each cycle, pulls **options chain**
   data (volume/open-interest ratio + call/put skew) as a "smart money"
   proxy, plus **implied volatility** on those contracts (shown alongside
   volume so you can see whether options are already "expensive" before
   considering one), and recent **news headlines** tagged by catalyst type
   (earnings, FDA, upgrade/downgrade, M&A, etc.).
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

There are **two alert tiers**, both under `alerts:` in `config/settings.yaml`:

- **General** (`alerts.alpha_score_threshold`, default 80) - a heads-up
  whenever a ticker's Alpha Score crosses this bar. Subject line starts
  with "Stock alert:".
- **BUY Setup** (`alerts.buy_setup`, default threshold 88) - a stricter,
  separate tier that only fires when Alpha Score clears its own higher
  bar **and** the breakout is bullish **and** has actually *held* for
  `scoring.breakout.hold_confirm_minutes` (default 15 min) - not just
  triggered once. Subject line starts with "BUY Setup:". Both tiers can
  fire independently for the same ticker (each has its own cooldown), and
  either can be turned off on its own via its `enabled` flag.

Despite the name, **"BUY Setup" describes a stronger combination of
signals the tool tracks - it is not a trade instruction.** It doesn't know
your risk tolerance, position sizing, or account size, and it has no
track record proving these signals predict a good trade. Do your own
analysis (and consider your own stop-loss/risk plan) before acting on
either alert.

Alerts are off by default until you configure real credentials (the shipped
`app_password: "CHANGE_ME"` in `settings.yaml` deliberately disables
sending). **Never put your real App Password in `config/settings.yaml`** -
that file is tracked by git and would end up committed. Instead:

1. Create a **Gmail App Password**: Google Account -> Security -> 2-Step
   Verification -> App passwords. (Requires 2-Step Verification to be
   enabled on the account.) This is a 16-character password scoped to one
   app - not your real Gmail password, and you can revoke it any time.
2. Copy the secrets template and edit the copy:
   ```bash
   cp config/secrets.yaml.example config/secrets.yaml
   ```
   ```yaml
   # config/secrets.yaml - git-ignored, never committed
   alerts:
     email:
       from_address: "you@gmail.com"
       app_password: "xxxx xxxx xxxx xxxx"   # the App Password from step 1
       to_address: "you@gmail.com"           # can be a different address
   ```
   `config/secrets.yaml` is layered on top of `config/settings.yaml` at
   startup (see `app/config.py`), so you only need to list the keys you're
   overriding. `config/settings.yaml` still controls `enabled`,
   `alpha_score_threshold`, and `cooldown_minutes`.
3. Restart `python main.py`.

Using a non-Gmail provider works too - just override `smtp_host`/
`smtp_port` in `config/secrets.yaml` as well; `app_password` becomes
whatever credential that provider uses for SMTP login.

**Note:** this alert is delivered by email only, from wherever `main.py` is
running. There's no push notification, SMS, or "live" dashboard hosted
outside your machine - the web dashboard at `http://127.0.0.1:8000` and
this email are the only two outputs.

## Dashboard

- **Top Alpha** - all tracked tickers ranked by Alpha Score.
- **Pre-Market** / **Intraday** - ranked by session.
- **Options Flow** - tickers with unusual options volume/open-interest,
  plus implied volatility (IV) per contract.
- **News** - recent tagged headlines for enriched tickers.

The Breakout column shows a small "held Xm" badge once a level break has
lasted long enough to be marked confirmed (green), or just "Xm" while it's
still fresh. Click any row to open a drill-down panel with the full score
breakdown (including hold time and average IV), recent headlines, and
flagged option contracts. The dashboard polls for new data every 30
seconds.

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
  and simply show no options score. Implied volatility comes from the same
  free chain data and can be stale or 0 for illiquid contracts.
- **Breakout hold tracking resets if a level stops holding, or if you edit
  `hold_confirm_minutes` mid-session** (existing hold timers aren't
  retroactively re-evaluated) - but it does survive an app restart, since
  it's read from the database each cycle, not kept in memory.
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
