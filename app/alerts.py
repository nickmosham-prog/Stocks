"""Email alerts. Two tiers, both configured under `alerts` in
config/settings.yaml:

- **general**: fires when a ticker's Alpha Score crosses `alpha_score_threshold`.
- **buy_setup**: a stricter, separate tier under `alerts.buy_setup` - fires
  only when Alpha Score clears a higher bar AND the breakout is bullish AND
  it has actually *held* (see scoring.breakout.hold_confirm_minutes), not
  just triggered once. Runs alongside the general tier, doesn't replace it.

Each tier sends at most one summary email per scan cycle (not one per
ticker), with its own independent per-symbol cooldown, so a general
heads-up on a symbol doesn't block (or get blocked by) a later BUY Setup
email for that same symbol.

This is a screening tool, not investment advice: "BUY Setup" describes a
stronger combination of signals the tool tracks, not an instruction.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from app import db
from app.config import load_settings
from app.scan import buy_setup
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


def _format_email(qualifying: list[dict]) -> tuple[str, str]:
    if len(qualifying) == 1:
        subject = f"Stock alert: {qualifying[0]['symbol']} Alpha Score {qualifying[0]['alpha_score']:.0f}"
    else:
        symbols = ", ".join(r["symbol"] for r in qualifying)
        subject = f"Stock alert: {len(qualifying)} tickers above threshold ({symbols})"

    body = _format_table(qualifying)
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


def _send_email(email_cfg: dict, subject: str, body: str) -> bool:
    msg = MIMEText(body)
    msg["Subject"] = subject
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


def process_alerts(rows: list[dict]) -> None:
    """Given every scored row from a scan cycle, run both alert tiers."""
    settings = load_settings()
    if not settings.get("alerts", "enabled", default=False):
        return

    email_cfg = settings.get("alerts", "email", default={})
    if not _is_configured(email_cfg):
        return

    now = datetime.now(tz=timezone.utc)

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
