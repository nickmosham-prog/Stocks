"""Options trade recommendation: for a ticker meeting the BUY Setup
(bullish) or BREAKDOWN Setup (bearish) criteria, select one specific
contract - not a screen of many candidates, one recommendation - following
a standard theta-mitigation convention: target ~30-45 days to expiration
(avoids the steepest final-2-weeks decay) and a moderately in-the-money
delta (real directional exposure without deep-OTM lottery-ticket decay).

This is a heuristic selector built on the simplified Black-Scholes model in
app/scan/options_greeks.py - not a guarantee of profitability or real-world
theta behavior. See the disclaimer in app/alerts.py's options_trade email.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from app.config import load_settings
from app.datasource.base import DataSource
from app.scan.options_greeks import compute_greeks

log = logging.getLogger(__name__)


def _expiration_in_window(expiration: str, today: date, dte_min: int, dte_max: int) -> bool:
    try:
        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
    except ValueError:
        return False
    dte = (exp_date - today).days
    return dte_min <= dte <= dte_max


def select_contract(
    contracts: list[dict],
    spot_price: float,
    direction: str,
    today: date | None = None,
    settings=None,
) -> dict | None:
    """Given a chain already restricted to the target DTE window, pick one
    contract. Never raises - returns None if nothing qualifies."""
    settings = settings or load_settings()
    cfg = settings.get("scoring", "options_strategy", default={})
    today = today or date.today()
    option_type = "call" if direction == "bullish" else "put"
    target_delta = (
        cfg.get("target_delta_call", 0.65) if option_type == "call" else cfg.get("target_delta_put", -0.65)
    )
    band = cfg.get("delta_band", 0.10)
    rate = cfg.get("risk_free_rate", 0.045)
    dte_min = cfg.get("dte_min", 30)
    dte_max = cfg.get("dte_max", 45)
    min_open_interest = cfg.get("min_open_interest", 50)
    min_volume = cfg.get("min_volume", 10)

    candidates = []
    for c in contracts:
        if c.get("option_type") != option_type:
            continue
        if not _expiration_in_window(c.get("expiration", ""), today, dte_min, dte_max):
            continue
        exp_date = datetime.strptime(c["expiration"], "%Y-%m-%d").date()
        dte = (exp_date - today).days
        greeks = compute_greeks(spot_price, c.get("strike"), dte, c.get("implied_volatility"), option_type, rate)
        if greeks is None or abs(greeks["delta"] - target_delta) > band:
            continue
        price = c.get("ask") or c.get("last_price")
        if not price:
            continue
        if (c.get("open_interest") or 0) < min_open_interest or (c.get("volume") or 0) < min_volume:
            continue
        candidates.append(
            {
                **c,
                "delta": greeks["delta"],
                "theta": greeks["theta"],
                "days_to_expiration": dte,
                "price": price,
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda c: (c.get("open_interest") or 0, c.get("volume") or 0, -abs(c["delta"] - target_delta)),
        reverse=True,
    )
    return candidates[0]


def find_recommended_contract(
    source: DataSource, symbol: str, spot_price: float, direction: str, settings=None
) -> dict | None:
    """Fetches expirations, filters to the configured DTE window, fetches
    that chain, and selects one contract. Returns None (never raises) when
    nothing qualifies - a missing/thin options chain is common and
    expected, not an error."""
    settings = settings or load_settings()
    cfg = settings.get("scoring", "options_strategy", default={})
    dte_min = cfg.get("dte_min", 30)
    dte_max = cfg.get("dte_max", 45)

    expirations = source.get_option_expirations(symbol)
    if not expirations:
        return None

    today = date.today()
    window = [e for e in expirations if _expiration_in_window(e, today, dte_min, dte_max)]
    if not window:
        log.info("no expirations in the %d-%d DTE window for %s", dte_min, dte_max, symbol)
        return None

    contracts = source.get_option_chain(symbol, expirations=window)
    if not contracts:
        return None

    return select_contract(contracts, spot_price, direction, today=today, settings=settings)
