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
6. Ranks the best bullish swing ideas into **Top Picks**, with reasons,
   risks, and a suggested call option for each (see **Top Picks** below),
   emailed as a digest twice a day.
7. Checks confirmed bullish breakouts against a **fundamentals quality
   gate** (profitable, reasonable valuation, growing revenue - see
   **Fundamentals quality gate** below) before calling them a BUY Setup, so
   the screener's strongest signal reflects real financials, not just
   price/volume narrative.
8. For a qualifying BUY Setup or BREAKDOWN Setup, selects one specific
   **options trade recommendation** (an exact contract - strike,
   expiration, price) using real option Greeks (see **Options trade
   recommendations** below).
9. Emails you when a ticker's Alpha Score crosses a threshold, or when a
   BUY/BREAKDOWN Setup or options trade recommendation fires (see
   **Email alerts** below) - one summary email per cycle per tier, not one
   per ticker, with a per-symbol cooldown so a hot name doesn't spam you.

Every night after the close, it rebuilds the historical volume baseline
used for RVOL and refreshes prior-day OHLC/ATR for the breakout scorer.
Fundamentals are refreshed once a day (they don't change intraday) rather
than every scan cycle.

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

There are **four alert tiers**, all under `alerts:` in `config/settings.yaml`:

- **General** (`alerts.alpha_score_threshold`, default 80) - a heads-up
  whenever a ticker's Alpha Score crosses this bar. Subject line starts
  with "Unusual activity:" and names the direction (e.g. "NFLX DOWN 5.9%").
  **This is not a buy signal** - the Alpha Score rewards big moves in either
  direction, so a crash can score as high as a rally. For buy ideas, use
  **Top Picks** (below).
- **BUY Setup** (`alerts.buy_setup`, default threshold 88) - a stricter,
  separate tier that only fires when Alpha Score clears its own higher
  bar **and** the breakout is bullish **and** has actually *held* for
  `scoring.breakout.hold_confirm_minutes` (default 15 min) **and** the
  ticker passes the **fundamentals quality gate** (see below) - not just a
  fresh price/volume trigger. Subject line starts with "BUY Setup:".
- **BREAKDOWN Setup** (`alerts.breakdown_setup`, default threshold 88) -
  the bearish mirror of BUY Setup: high Alpha Score **and** a bearish
  breakdown that has held. It does **not** require the fundamentals gate
  to pass - a company with weak financials is often exactly what a bearish
  thesis looks like, so gating on "good fundamentals" would be backwards
  here. Subject line starts with "BREAKDOWN Setup:".
- **Options trade** (`alerts.options_trade`, default cooldown 240 min) -
  fires alongside a BUY or BREAKDOWN Setup when a specific option contract
  was selected for it (see **Options trade recommendations** below),
  naming the exact strike/expiration/price. Subject line starts with
  "Options trade:".

All four tiers can fire independently for the same ticker (each has its
own cooldown), and each can be turned off on its own via its `enabled`
flag.

Despite the names, **"BUY Setup"/"BREAKDOWN Setup"/the options trade
recommendation describe combinations of signals the tool tracks - none of
them are a trade instruction.** The tool doesn't know your risk tolerance,
position sizing, or account size, and it has no track record proving these
signals predict a good trade. Do your own analysis (and consider your own
stop-loss/risk plan) before acting on any alert.

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

## Top Picks (the main buy-idea output)

Top Picks is a ranked list of up to 5 bullish **swing-trade ideas** (days
to weeks), each with plain-English reasons, risks, and a suggested call
option. It is shown on the dashboard's default **Top Picks** tab (updated
every scan) and emailed as a digest at **10:00 and 15:00 ET** on weekdays.

**Who can be picked** - all three must be true:
- passes the **fundamentals check** (profitable, reasonable P/E, growing
  revenue - see below). This also excludes ETFs.
- is **above its 50-day moving average** (in an uptrend)
- is **up on the day** - a stock falling on heavy volume (like a news
  crash) can never be a pick

**How they're ranked** - a 0-100 **Pick Score** blending:
| Component | Weight | What scores well |
|---|---|---|
| Trend | 30% | above 50- and 200-day averages, near its 52-week high, beating the S&P 500 over 3 months |
| Momentum | 25% | up today on above-normal volume, breaking/holding above yesterday's high |
| Quality | 25% | revenue growth, profit margin, reasonable P/E |
| Analyst | 10% | upside to Wall Street's average price target, consensus rating |
| Options | 10% | call volume outweighing put volume |

Only stocks scoring at least `picks.min_score` (default 55) are listed, so a
weak or choppy day can produce fewer than 5 picks, or none. The digest still
arrives on those days and says so, so a quiet inbox never leaves you
wondering whether the app is running.

**Each pick's option idea** uses the same 30-45 day, ~0.65 delta call
selection described in **Options trade recommendations** below. The idea
includes the approximate cost per contract (100 shares), the daily time
decay, and the **breakeven price at expiration** (strike + premium) with the
% move needed to get there. The risks list flags **earnings before the
option expires**, since options often lose value right after earnings even
when the stock moves your way. It also flags a stock stretched far above its
50-day average, and thin options liquidity.

Everything is tunable under `picks:` in `config/settings.yaml` (count, score
floor, digest times, component weights). These are screening ideas, not
trade instructions: check live quotes with your broker, size positions so
losing the whole option premium is acceptable, and decide your exit before
you enter.

**First start after upgrading:** the app downloads ~400 days of daily
history (needed for the 200-day average and 52-week high) plus the extra
fundamentals fields, once. This can take several minutes before the
dashboard comes up; later starts are fast.

## Fundamentals quality gate

The BUY Setup tier (email and dashboard badge) additionally requires the
ticker to pass a basic quality filter on real company financials, checked
against free data from `yfinance` (`scoring.fundamentals` in
`config/settings.yaml`, all on by default):

- **Profitable** - positive trailing net income.
- **Reasonable valuation** - trailing P/E between `min_pe` (default 0) and
  `max_pe` (default 60).
- **Growing revenue** - positive year-over-year revenue growth (from
  Yahoo's own figure, or computed from quarterly income statements as a
  fallback when Yahoo's isn't available).

Each ticker gets a **pass / fail / unknown** status, refreshed once daily
(fundamentals don't move intraday) rather than every scan cycle:

- **pass** - all required checks succeeded.
- **fail** - at least one required check ran and failed.
- **unknown** - a required field simply wasn't available for this ticker
  (common for newer listings, ADRs, or thinly-covered names). **Unknown is
  treated the same as fail for gating purposes** - an unverifiable stock
  never gets a BUY badge just because data happened to be missing - but is
  shown distinctly in the dashboard drill-down so you can tell "this
  failed the bar" apart from "this tool couldn't check."

This gate applies **only** to the bullish BUY Setup tier, not BREAKDOWN
Setup (see Email alerts above for why). You can loosen or disable
individual checks (or the whole gate, via `alerts.buy_setup.require_fundamentals: false`)
in `config/settings.yaml`.

## Options trade recommendations

For any ticker that qualifies for a BUY Setup or BREAKDOWN Setup, the
scanner picks **one specific option contract** - not a screen of many
candidates - following a standard theta-mitigation convention used to
balance real directional exposure against time decay:

- **30-45 days to expiration** by default (`scoring.options_strategy.dte_min`/
  `dte_max`) - avoids the steepest final-two-weeks decay while staying
  short enough to react to a changing thesis.
- **~0.65 delta, moderately in-the-money** by default
  (`target_delta_call`/`target_delta_put`, `delta_band`) - meaningful
  directional exposure without deep-out-of-the-money "lottery ticket"
  decay.
- **Calls on confirmed bullish (BUY Setup) tickers, puts on confirmed
  bearish (BREAKDOWN Setup) tickers.**
- Filtered for basic liquidity (`min_open_interest`, `min_volume`) so the
  pick isn't an illiquid contract you can't actually trade near its quoted
  price.

Delta and theta are computed with a **simplified Black-Scholes model**
(constant volatility, no dividends, European-style exercise assumed) - a
heuristic for contract selection, not a precise pricing engine. If nothing
in the target window/delta band/liquidity bar qualifies (common - not
every ticker has listed options in that exact range), no recommendation is
made for that ticker; this is expected, not an error. In that case the
dashboard and digest show the **reason** instead of a blank, e.g. "No
expiration 30-45 days out" or "No put near 0.65 delta with enough open
interest".

A contract counts as liquid enough if it has either enough open interest
(`min_open_interest`) **or** enough volume today (`min_volume`). In-the-money
options often trade only a few times a day while still being widely held.
Yahoo's implied-volatility figure is often junk for in-the-money strikes
(near 0% or several hundred %). When that happens, the model uses the
typical implied volatility of near-the-money strikes for the same
expiration instead of throwing the contract away.

**This is not a trade instruction.** Verify pricing and Greeks with your
broker before acting - the same disclaimer appears in the dashboard's
Options Trades tab and in the options-trade alert email. Turn it off
entirely via `scoring.options_strategy.enabled: false`.

## Dashboard

- **Top Picks** (default tab) - the ranked bullish ideas described above,
  as cards with reasons, risks, and the suggested call option.
- **Buy Signals** - only tickers currently meeting the BUY
  or BREAKDOWN Setup criteria, with columns emphasizing *why*: direction,
  Alpha Score, hold time, fundamentals status, and the recommended option
  contract (if one was found).
- **Top Alpha** - all tracked tickers ranked by Alpha Score.
- **Pre-Market** / **Intraday** - ranked by session.
- **Options Flow** - tickers with unusual options volume/open-interest,
  plus implied volatility (IV) per contract.
- **Options Trades** - the current recommended contract per qualifying
  ticker (see **Options trade recommendations** above), with the same
  disclaimer shown in its alert email.
- **News** - recent tagged headlines for enriched tickers.

The Breakout column shows a small "held Xm" badge once a level break has
lasted long enough to be marked confirmed (green), or just "Xm" while it's
still fresh. A ticker meeting the **BUY Setup** criteria (see Email alerts
above) gets a green "BUY" tag next to its symbol and a green accent on the
row; one meeting **BREAKDOWN Setup** gets a red "BREAKDOWN" tag and accent.
Both badges are computed by the same functions that decide whether to send
the corresponding alert email, so the badges and the emails never
disagree. This works whether or not email is configured; it's controlled
by `alerts.buy_setup.enabled`/`alerts.breakdown_setup.enabled` in
`config/settings.yaml` independently of whether alert emails are set up.
Click any row to open a drill-down panel with the full score breakdown
(including hold time, average IV, fundamentals, analyst target, trend
metrics, and the recommended contract if any), recent headlines, and flagged option contracts. The
dashboard polls for new data every 30 seconds.

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
- **Fundamentals data quality mirrors `yfinance`'s own coverage** - newer
  listings, ADRs, and thinly-covered names often show "unknown" rather
  than pass/fail, and unknown is treated as not-passing (see
  **Fundamentals quality gate** above). It's a basic sanity filter
  (profitable, reasonable P/E, growing revenue), not deep fundamental
  analysis.
- **Options trade recommendations are a heuristic**, not a pricing engine
  or brokerage-grade Greeks calculation - see **Options trade
  recommendations** above. A recommendation may not exist for every
  qualifying ticker (no listed options in the target window, or nothing
  clears the delta/liquidity bar).
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
