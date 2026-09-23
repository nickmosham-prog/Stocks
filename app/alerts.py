"""Email alerts. Four per-scan tiers, all configured under `alerts` in
config/settings.yaml, plus the scheduled Top Picks digest (`picks` in
config/settings.yaml, see send_picks_digest below):

- **general**: fires when a ticker's Alpha Score crosses `alpha_score_threshold`.
- **buy_setup**: a stricter tier - Alpha Score clears a higher bar AND the
  breakout is bullish AND has actually *held* (see
  scoring.breakout.hold_confirm_minutes) AND real financials pass a quality
  gate (scoring.fundamentals) - not just a fresh trigger.
- **breakdown_setup**: the bearish mirror of buy_setup (no fundamentals gate
  - a bad-fundamentals company is often exactly what a bearish thesis looks
  like, not a disqualifier).
- **options_trade**: fires alongside buy_setup/breakdown_setup when a
  specific option contract was selected for the ticker (see
  app/scan/options_strategy.py) - names the exact strike/expiration/price.

Each tier sends at most one summary email per scan cycle (not one per
ticker), with its own independent per-symbol cooldown, so tiers never
block each other for the same symbol.

This is a screening tool, not investment advice: "BUY Setup"/"BREAKDOWN
Setup"/the options trade recommendation describe combinations of signals
the tool tracks, not instructions.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText

from app import db
from app.config import load_settings
from app.scan import breakdown_setup, buy_setup
from app.scan.numeric import is_valid

log = logging.getLogger(__name__)

_warned_unconfigured = False


def _last_alert_time(symbol: str, kind: str) -> datetime | None:
    with db.cursor() as cur:
        cur.execute(
            "SELECT sent_at FROM alert_log WHERE symbol = ? AND kind = ? ORDER BY sent_at DESC LIMIT 1",
            (symbol, kind),
        )
        row = cur.fetchone()
    return datetime.fromisoformat(row["sent_at"]) if row else None


def _record_alert(symbol: str, alpha_score: float, sent_at: datetime, kind: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alert_log (symbol, kind, alpha_score, sent_at) VALUES (?, ?, ?, ?)",
            (symbol, kind, alpha_score, sent_at.isoformat()),
        )


def _is_configured(email_cfg: dict) -> bool:
    global _warned_unconfigured
    app_password = email_cfg.get("app_password", "")
    from_address = email_cfg.get("from_address", "")
    configured = bool(app_password) and app_password != "CHANGE_ME" and "@" in from_address
    if not configured and not _warned_unconfigured:
        log.warning(
            "alerts.email is not configured (app_password is blank/CHANGE_ME) - "
            "skipping email alerts. Edit config/settings.yaml to enable them."
        )
        _warned_unconfigured = True
    return configured


def _format_table(qualifying: list[dict]) -> str:
    lines = ["Ticker".ljust(8) + "Alpha".rjust(7) + "  Price".rjust(9) + "   RVOL".rjust(8) + "  Gap%".rjust(8) + "  Session"]
    lines.append("-" * 60)
    for r in qualifying:
        price = f"${r['price']:.2f}" if r.get("price") is not None else "n/a"
        rvol_str = f"{r['rvol']:.1f}x" if r.get("rvol") is not None else "n/a"
        gap_str = f"{r['gap_pct']:+.1f}%" if r.get("gap_pct") is not None else "n/a"
        lines.append(
            f"{r['symbol']:<8}{r['alpha_score']:>7.0f}{price:>10}{rvol_str:>9}{gap_str:>9}  {r['session']}"
        )
    return "\n".join(lines)


def _direction_label(row: dict) -> str:
    gap = row.get("gap_pct")
    if not is_valid(gap):
        return row["symbol"]
    return f"{row['symbol']} {'UP' if gap >= 0 else 'DOWN'} {abs(gap):.1f}%"


def _format_email(qualifying: list[dict]) -> tuple[str, str]:
    # Direction goes in the subject: the Alpha Score rewards big moves either
    # way, so a crash can score as high as a rally - this must never read
    # like a buy signal.
    if len(qualifying) == 1:
        subject = f"Unusual activity: {_direction_label(qualifying[0])} (Alpha {qualifying[0]['alpha_score']:.0f})"
    else:
        labels = ", ".join(_direction_label(r) for r in qualifying)
        subject = f"Unusual activity: {len(qualifying)} tickers ({labels})"

    body = (
        "Heads-up only: unusual volume/price activity, in EITHER direction.\n"
        "This is not a buy signal - see the Top Picks email/tab for buy ideas.\n\n"
    )
    body += _format_table(qualifying)
    body += "\n\nFree/delayed data via Yahoo Finance. Not financial advice."
    return subject, body


def _format_buy_setup_email(qualifying: list[dict]) -> tuple[str, str]:
    if len(qualifying) == 1:
        subject = f"BUY Setup: {qualifying[0]['symbol']} Alpha Score {qualifying[0]['alpha_score']:.0f}, breakout confirmed"
    else:
        symbols = ", ".join(r["symbol"] for r in qualifying)
        subject = f"BUY Setup: {len(qualifying)} confirmed breakouts ({symbols})"

    body = (
        "Stronger combined signal: high Alpha Score + bullish breakout that has\n"
        "actually held (not just triggered once).\n\n"
    )
    body += _format_table(qualifying)
    body += "\n\nHold time:\n"
    for r in qualifying:
        minutes = round(r["breakout_hold_minutes"]) if r.get("breakout_hold_minutes") is not None else "?"
        body += f"  {r['symbol']}: held {minutes}m above the prior day's high\n"
    body += (
        "\nThis is a screening signal describing what the tool tracks, not a\n"
        "trade instruction - do your own analysis before acting.\n"
        "Free/delayed data via Yahoo Finance. Not financial advice."
    )
    return subject, body


def _format_breakdown_setup_email(qualifying: list[dict]) -> tuple[str, str]:
    if len(qualifying) == 1:
        subject = f"BREAKDOWN Setup: {qualifying[0]['symbol']} Alpha Score {qualifying[0]['alpha_score']:.0f}, breakdown confirmed"
    else:
        symbols = ", ".join(r["symbol"] for r in qualifying)
        subject = f"BREAKDOWN Setup: {len(qualifying)} confirmed breakdowns ({symbols})"

    body = (
        "Stronger combined signal: high Alpha Score + bearish breakdown that\n"
        "has actually held (not just triggered once).\n\n"
    )
    body += _format_table(qualifying)
    body += "\n\nHold time:\n"
    for r in qualifying:
        minutes = round(r["breakout_hold_minutes"]) if r.get("breakout_hold_minutes") is not None else "?"
        body += f"  {r['symbol']}: held {minutes}m below the prior day's low\n"
    body += (
        "\nThis is a screening signal describing what the tool tracks, not a\n"
        "trade instruction - do your own analysis before acting.\n"
        "Free/delayed data via Yahoo Finance. Not financial advice."
    )
    return subject, body


def _format_options_trade_email(qualifying: list[dict], trade_recs: dict[str, dict]) -> tuple[str, str]:
    if len(qualifying) == 1:
        symbol = qualifying[0]["symbol"]
        contract = trade_recs[symbol]
        subject = f"Options trade: {symbol} {contract['option_type'].upper()} ${contract['strike']:.2f} exp {contract['expiration']}"
    else:
        symbols = ", ".join(r["symbol"] for r in qualifying)
        subject = f"Options trade: {len(qualifying)} recommended contracts ({symbols})"

    lines = [
        "Ticker".ljust(8) + "Type".ljust(6) + "Strike".rjust(8) + "  Expiration".ljust(13)
        + "   DTE".rjust(6) + "   Price".rjust(9) + "  Delta".rjust(8) + "  Daily Theta".rjust(13)
    ]
    lines.append("-" * 76)
    for r in qualifying:
        c = trade_recs[r["symbol"]]
        lines.append(
            f"{r['symbol']:<8}{c['option_type'].upper():<6}{c['strike']:>8.2f}  "
            f"{c['expiration']:<11}{c['days_to_expiration']:>6}d"
            f"{'$' + format(c['price'], '.2f'):>9}{c['delta']:>8.2f}"
            f"{'$' + format(c['theta'], '.2f'):>13}"
        )
    body = "\n".join(lines)
    body += (
        "\n\nHeuristic contract selection using a simplified Black-Scholes\n"
        "pricing model (assumes constant volatility, ignores dividends and\n"
        "early exercise; the risk-free rate is an approximation). Not a\n"
        "guarantee of theta behavior or profitability, and not a trade\n"
        "instruction - verify pricing and Greeks with your broker before\n"
        "acting. Free/delayed data via Yahoo Finance. Not financial advice."
    )
    return subject, body


def _send_email(email_cfg: dict, subject: str, body: str) -> bool:
    # Explicit UTF-8 for body and subject: smtplib encodes a plain str
    # message as ASCII and would crash on any non-ASCII character.
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = email_cfg["from_address"]
    msg["To"] = email_cfg["to_address"]
    try:
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg.get("smtp_port", 587), timeout=15) as server:
            server.starttls()
            server.login(email_cfg["from_address"], email_cfg["app_password"])
            server.sendmail(email_cfg["from_address"], [email_cfg["to_address"]], msg.as_string())
        return True
    except Exception:
        log.exception("failed to send alert email")
        return False


def _process_tier(
    rows: list[dict],
    email_cfg: dict,
    kind: str,
    threshold: float,
    cooldown: timedelta,
    now: datetime,
    extra_filter,
    format_fn,
) -> None:
    qualifying = []
    for row in rows:
        alpha = row.get("alpha_score")
        # is_valid, not a bare None-check: `float('nan') is not None` is
        # True and `nan < threshold` is False, so a NaN score would
        # otherwise sail straight past this guard and fire an alert.
        if not is_valid(alpha) or alpha < threshold:
            continue
        if extra_filter is not None and not extra_filter(row):
            continue
        last = _last_alert_time(row["symbol"], kind)
        if last is not None and (now - last) < cooldown:
            continue
        qualifying.append(row)

    if not qualifying:
        return

    qualifying.sort(key=lambda r: r["alpha_score"], reverse=True)
    subject, body = format_fn(qualifying)
    if _send_email(email_cfg, subject, body):
        for row in qualifying:
            _record_alert(row["symbol"], row["alpha_score"], now, kind)
        log.info(
            "sent %s alert email for %d ticker(s): %s",
            kind, len(qualifying), ", ".join(r["symbol"] for r in qualifying),
        )


def process_alerts(rows: list[dict], trade_recs: dict[str, dict] | None = None) -> None:
    """Given every scored row from a scan cycle, run all alert tiers.
    `trade_recs` (symbol -> recommended contract dict) drives the
    options_trade tier - omit/None if the options strategy engine is
    disabled or found nothing this cycle."""
    settings = load_settings()
    if not settings.get("alerts", "enabled", default=False):
        return

    email_cfg = settings.get("alerts", "email", default={})
    if not _is_configured(email_cfg):
        return

    now = datetime.now(tz=timezone.utc)
    trade_recs = trade_recs or {}

    _process_tier(
        rows,
        email_cfg,
        kind="general",
        threshold=settings.get("alerts", "alpha_score_threshold", default=80),
        cooldown=timedelta(minutes=settings.get("alerts", "cooldown_minutes", default=60)),
        now=now,
        extra_filter=None,
        format_fn=_format_email,
    )

    buy_cfg = settings.get("alerts", "buy_setup", default={})
    if buy_cfg.get("enabled", False):
        _process_tier(
            rows,
            email_cfg,
            kind="buy_setup",
            threshold=buy_cfg.get("alpha_score_threshold", 88),
            cooldown=timedelta(minutes=buy_cfg.get("cooldown_minutes", 60)),
            now=now,
            extra_filter=lambda row: buy_setup.meets_buy_setup_criteria(row, settings),
            format_fn=_format_buy_setup_email,
        )

    breakdown_cfg = settings.get("alerts", "breakdown_setup", default={})
    if breakdown_cfg.get("enabled", False):
        _process_tier(
            rows,
            email_cfg,
            kind="breakdown_setup",
            threshold=breakdown_cfg.get("alpha_score_threshold", 88),
            cooldown=timedelta(minutes=breakdown_cfg.get("cooldown_minutes", 60)),
            now=now,
            extra_filter=lambda row: breakdown_setup.meets_breakdown_setup_criteria(row, settings),
            format_fn=_format_breakdown_setup_email,
        )

    options_trade_cfg = settings.get("alerts", "options_trade", default={})
    if options_trade_cfg.get("enabled", False) and trade_recs:
        _process_tier(
            rows,
            email_cfg,
            kind="options_trade",
            threshold=0,  # gating already happened via buy/breakdown_signal + trade_recs membership
            cooldown=timedelta(minutes=options_trade_cfg.get("cooldown_minutes", 240)),
            now=now,
            extra_filter=lambda row: row["symbol"] in trade_recs,
            format_fn=lambda qualifying: _format_options_trade_email(qualifying, trade_recs),
        )


# --- Top Picks digest -------------------------------------------------------

DIGEST_KIND = "picks_digest"
DIGEST_MAX_SCAN_AGE_MINUTES = 20


def _fmt_money(value) -> str:
    return f"${value:,.2f}" if is_valid(value) else "n/a"


def _format_pick(pick: dict) -> str:
    lines = [
        f"#{pick['rank']} {pick['symbol']}  {_fmt_money(pick.get('price'))}   "
        f"Pick Score {pick['pick_score']:.0f}/100"
    ]
    reasons = [r for r in (pick.get("reasons") or "").split("\n") if r]
    risks = [r for r in (pick.get("risks") or "").split("\n") if r]
    if reasons:
        lines.append("  Why:")
        lines.extend(f"   - {r}" for r in reasons)
    if risks:
        lines.append("  Risks:")
        lines.extend(f"   - {r}" for r in risks)

    if pick.get("contract_symbol") and is_valid(pick.get("strike")) and is_valid(pick.get("option_price")):
        per_contract = pick["option_price"] * 100
        lines.append(
            f"  Option idea: CALL ${pick['strike']:g} expiring {pick['expiration']} "
            f"({pick['days_to_expiration']} days), about {_fmt_money(pick['option_price'])}/share "
            f"(~${per_contract:,.0f} per contract)"
        )
        detail = []
        if is_valid(pick.get("delta")):
            detail.append(f"delta {pick['delta']:.2f}")
        if is_valid(pick.get("theta")):
            detail.append(f"time decay ~{_fmt_money(abs(pick['theta']))}/share per day")
        if is_valid(pick.get("breakeven")):
            move = pick.get("breakeven_move_pct")
            move_text = f" ({move:+.1f}% from here)" if is_valid(move) else ""
            detail.append(f"breakeven {_fmt_money(pick['breakeven'])} at expiration{move_text}")
        if detail:
            lines.append("     " + ", ".join(detail))
        lines.append(f"     contract: {pick['contract_symbol']}")
    else:
        lines.append("  Option idea: none - no liquid 30-45 day call near 0.65 delta; stock-only idea")
    return "\n".join(lines)


def _format_picks_digest(slot_label: str, picks: list[dict], run: dict | None) -> tuple[str, str]:
    footer = (
        "\n\nHow picks are chosen: each one passed the fundamentals check (profitable,\n"
        "reasonable P/E, growing revenue), is above its 50-day average, and is up\n"
        "today; they are ranked by trend, momentum, business quality, analyst\n"
        "targets and options activity. Swing-trade horizon: days to weeks.\n\n"
        "Screening ideas, not trade instructions. Option prices/Greeks come from\n"
        "free delayed data and a simplified Black-Scholes model - check live\n"
        "quotes with your broker, size positions so a total loss of the premium\n"
        "is acceptable, and decide your exit before you enter.\n"
        "Free/delayed data via Yahoo Finance. Not financial advice."
    )
    if run is None:
        subject = f"Top Picks {slot_label} ET: no fresh scan data"
        body = (
            f"No scan has completed in the last {DIGEST_MAX_SCAN_AGE_MINUTES} minutes, so there are\n"
            "no current picks. The app is running, but scans may be failing (e.g. Yahoo\n"
            "Finance rate-limiting). Check logs/stocks.log on your Mac."
        )
        return subject, body + footer

    scan_time = datetime.fromisoformat(run["scan_ts"]).strftime("%H:%M")
    if not picks:
        subject = f"Top Picks {slot_label} ET: no stock cleared the bar"
        body = (
            f"No stock cleared the Top Picks bar at the {scan_time} ET scan "
            f"({run.get('candidates') or 0} passed the basic\n"
            "filters, none scored high enough). That usually means a weak or choppy\n"
            "tape - sitting out is a valid position. The dashboard keeps updating\n"
            "every 5 minutes."
        )
        return subject, body + footer

    subject = f"Top Picks {slot_label} ET: " + ", ".join(p["symbol"] for p in picks)
    body = f"Top Picks as of the {scan_time} ET scan - ranked bullish swing ideas.\n\n"
    body += "\n\n".join(_format_pick(p) for p in picks)
    return subject, body + footer


def _latest_pick_run(now: datetime) -> tuple[dict | None, list[dict]]:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM pick_runs ORDER BY scan_ts DESC LIMIT 1")
        run = cur.fetchone()
        if run is None:
            return None, []
        run = dict(run)
        age = now - datetime.fromisoformat(run["scan_ts"])
        if age > timedelta(minutes=DIGEST_MAX_SCAN_AGE_MINUTES):
            return None, []
        cur.execute("SELECT * FROM top_picks WHERE scan_ts = ? ORDER BY rank", (run["scan_ts"],))
        return run, [dict(r) for r in cur.fetchall()]


def send_picks_digest(slot_label: str, now: datetime | None = None) -> bool:
    """Emails the latest Top Picks. Always sends something (even "no
    picks") so a quiet inbox never leaves the user wondering whether the
    system is working. At most once per slot per day. Returns True if sent."""
    settings = load_settings()
    if not settings.get("alerts", "enabled", default=False) or not settings.get("picks", "enabled", default=True):
        return False
    email_cfg = settings.get("alerts", "email", default={})
    if not _is_configured(email_cfg):
        return False

    now = now or datetime.now(tz=timezone.utc)
    slot_key = f"DIGEST-{slot_label}"
    last = _last_alert_time(slot_key, DIGEST_KIND)
    if last is not None and (now - last) < timedelta(hours=12):
        return False

    run, picks = _latest_pick_run(now)
    subject, body = _format_picks_digest(slot_label, picks, run)
    if not _send_email(email_cfg, subject, body):
        return False
    _record_alert(slot_key, None, now, DIGEST_KIND)
    log.info("sent Top Picks digest (%s): %d pick(s)", slot_label, len(picks))
    return True
