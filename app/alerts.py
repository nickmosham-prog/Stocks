"""Email alert when a ticker's Alpha Score crosses a configurable threshold.

One email per scan cycle summarizing every newly-qualifying ticker (not one
email per ticker), with a per-symbol cooldown so the same name doesn't
re-alert every 5 minutes while it stays hot.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from app import db
from app.config import load_settings

log = logging.getLogger(__name__)

_warned_unconfigured = False


def _last_alert_time(symbol: str) -> datetime | None:
    with db.cursor() as cur:
        cur.execute(
            "SELECT sent_at FROM alert_log WHERE symbol = ? ORDER BY sent_at DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
    return datetime.fromisoformat(row["sent_at"]) if row else None


def _record_alert(symbol: str, alpha_score: float, sent_at: datetime) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alert_log (symbol, alpha_score, sent_at) VALUES (?, ?, ?)",
            (symbol, alpha_score, sent_at.isoformat()),
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


def _format_email(qualifying: list[dict]) -> tuple[str, str]:
    if len(qualifying) == 1:
        subject = f"Stock alert: {qualifying[0]['symbol']} Alpha Score {qualifying[0]['alpha_score']:.0f}"
    else:
        symbols = ", ".join(r["symbol"] for r in qualifying)
        subject = f"Stock alert: {len(qualifying)} tickers above threshold ({symbols})"

    lines = ["Ticker".ljust(8) + "Alpha".rjust(7) + "  Price".rjust(9) + "   RVOL".rjust(8) + "  Gap%".rjust(8) + "  Session"]
    lines.append("-" * 60)
    for r in qualifying:
        price = f"${r['price']:.2f}" if r.get("price") is not None else "n/a"
        rvol_str = f"{r['rvol']:.1f}x" if r.get("rvol") is not None else "n/a"
        gap_str = f"{r['gap_pct']:+.1f}%" if r.get("gap_pct") is not None else "n/a"
        lines.append(
            f"{r['symbol']:<8}{r['alpha_score']:>7.0f}{price:>10}{rvol_str:>9}{gap_str:>9}  {r['session']}"
        )
    lines.append("")
    lines.append("Free/delayed data via Yahoo Finance. Not financial advice.")
    return subject, "\n".join(lines)


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


def process_alerts(rows: list[dict]) -> None:
    """Given every scored row from a scan cycle, email any that newly cross
    the Alpha Score threshold (respecting the per-symbol cooldown)."""
    settings = load_settings()
    if not settings.get("alerts", "enabled", default=False):
        return

    email_cfg = settings.get("alerts", "email", default={})
    if not _is_configured(email_cfg):
        return

    threshold = settings.get("alerts", "alpha_score_threshold", default=80)
    cooldown = timedelta(minutes=settings.get("alerts", "cooldown_minutes", default=60))
    now = datetime.now(tz=timezone.utc)

    qualifying = []
    for row in rows:
        alpha = row.get("alpha_score")
        if alpha is None or alpha < threshold:
            continue
        last = _last_alert_time(row["symbol"])
        if last is not None and (now - last) < cooldown:
            continue
        qualifying.append(row)

    if not qualifying:
        return

    qualifying.sort(key=lambda r: r["alpha_score"], reverse=True)
    subject, body = _format_email(qualifying)
    if _send_email(email_cfg, subject, body):
        for row in qualifying:
            _record_alert(row["symbol"], row["alpha_score"], now)
        log.info("sent alert email for %d ticker(s): %s", len(qualifying), ", ".join(r["symbol"] for r in qualifying))
