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
import statistics
from datetime import date, datetime

from app.config import load_settings
from app.datasource.base import DataSource
from app.scan.numeric import is_valid
from app.scan.options_greeks import compute_greeks

log = logging.getLogger(__name__)


def _expiration_in_window(expiration: str, today: date, dte_min: int, dte_max: int) -> bool:
    try:
        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
    except ValueError:
        return False
    dte = (exp_date - today).days
    return dte_min <= dte <= dte_max


# yfinance's impliedVolatility is often junk for in-the-money strikes
# (0.00001, or several hundred percent). Outside this range it's treated as
# missing and replaced by the expiration's typical near-the-money IV.
IV_MIN, IV_MAX = 0.05, 3.0
NEAR_MONEY_PCT = 0.10


def _sane_iv(iv) -> bool:
    return is_valid(iv) and IV_MIN <= iv <= IV_MAX


def _fallback_ivs(contracts: list[dict], spot_price: float) -> dict[str, float]:
    """expiration -> median sane IV of strikes within +/-10% of spot."""
    by_expiration: dict[str, list[float]] = {}
    for c in contracts:
        strike, iv = c.get("strike"), c.get("implied_volatility")
        if not (is_valid(strike) and _sane_iv(iv)):
            continue
        if abs(strike / spot_price - 1.0) <= NEAR_MONEY_PCT:
            by_expiration.setdefault(c.get("expiration"), []).append(iv)
    return {exp: statistics.median(ivs) for exp, ivs in by_expiration.items()}


def _select(
    contracts: list[dict], spot_price: float, direction: str, today: date, settings
) -> tuple[dict | None, str | None]:
    cfg = settings.get("scoring", "options_strategy", default={})
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
    fallback = _fallback_ivs(contracts, spot_price) if is_valid(spot_price) and spot_price > 0 else {}

    in_window = 0
    candidates = []
    for c in contracts:
        if c.get("option_type") != option_type:
            continue
        if not _expiration_in_window(c.get("expiration", ""), today, dte_min, dte_max):
            continue
        in_window += 1
        exp_date = datetime.strptime(c["expiration"], "%Y-%m-%d").date()
        dte = (exp_date - today).days
        iv, iv_estimated = c.get("implied_volatility"), False
        if not _sane_iv(iv):
            iv, iv_estimated = fallback.get(c["expiration"]), True
        greeks = compute_greeks(spot_price, c.get("strike"), dte, iv, option_type, rate)
        if greeks is None or abs(greeks["delta"] - target_delta) > band:
            continue
        price = c.get("ask") or c.get("last_price")
        if not price:
            continue
        # Either measure is enough: in-the-money contracts often trade only a
        # handful of times a day while still carrying plenty of open interest.
        if (c.get("open_interest") or 0) < min_open_interest and (c.get("volume") or 0) < min_volume:
            continue
        candidates.append(
            {
                **c,
                "implied_volatility": iv,
                "iv_estimated": iv_estimated,
                "delta": greeks["delta"],
                "theta": greeks["theta"],
                "days_to_expiration": dte,
                "price": price,
            }
        )

    if not candidates:
        if not in_window:
            return None, f"No {option_type}s listed {dte_min}-{dte_max} days out"
        return None, f"No {option_type} near {abs(target_delta):.2f} delta with enough open interest"

    candidates.sort(
        key=lambda c: (c.get("open_interest") or 0, c.get("volume") or 0, -abs(c["delta"] - target_delta)),
        reverse=True,
    )
    return candidates[0], None


def select_contract(
    contracts: list[dict],
    spot_price: float,
    direction: str,
    today: date | None = None,
    settings=None,
) -> dict | None:
    """Given a chain already restricted to the target DTE window, pick one
    contract. Never raises - returns None if nothing qualifies."""
    contract, _ = _select(contracts, spot_price, direction, today or date.today(), settings or load_settings())
    return contract


def find_recommended_contract_with_reason(
    source: DataSource, symbol: str, spot_price: float, direction: str, settings=None
) -> tuple[dict | None, str | None]:
    """Fetches expirations, filters to the configured DTE window, fetches
    that chain, and selects one contract. Returns (contract, None), or
    (None, plain-English reason) when nothing qualifies - a missing/thin
    options chain is common and expected, not an error. Never raises."""
    settings = settings or load_settings()
    cfg = settings.get("scoring", "options_strategy", default={})
    dte_min = cfg.get("dte_min", 30)
    dte_max = cfg.get("dte_max", 45)

    expirations = source.get_option_expirations(symbol)
    if not expirations:
        return None, "No option expirations listed"

    today = date.today()
    window = [e for e in expirations if _expiration_in_window(e, today, dte_min, dte_max)]
    if not window:
        log.info("no expirations in the %d-%d DTE window for %s", dte_min, dte_max, symbol)
        return None, f"No expiration {dte_min}-{dte_max} days out"

    contracts = source.get_option_chain(symbol, expirations=window)
    if not contracts:
        return None, "Options chain unavailable"

    return _select(contracts, spot_price, direction, today, settings)


def find_recommended_contract(
    source: DataSource, symbol: str, spot_price: float, direction: str, settings=None
) -> dict | None:
    contract, _ = find_recommended_contract_with_reason(source, symbol, spot_price, direction, settings)
    return contract
