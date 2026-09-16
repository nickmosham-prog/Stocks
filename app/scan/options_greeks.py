"""Black-Scholes delta/theta approximation for the options trade selector
(app/scan/options_strategy.py). yfinance's option chain doesn't provide
Greeks directly, so they're computed here from strike/spot/implied
volatility/days-to-expiration.

This is a simplified pricing model: European-style exercise, constant
volatility, no dividends, and an approximate risk-free rate. It's a
heuristic filter for contract selection (targeting a delta range that
balances directional exposure against theta decay), not a precise pricing
engine - callers must not treat its output as investment advice or a
guarantee of real-world option behavior.

Uses math.erf for the normal CDF rather than adding a scipy dependency.
"""

from __future__ import annotations

import math

from app.scan.numeric import is_valid


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def compute_greeks(
    spot_price: float,
    strike: float,
    days_to_expiration: int,
    implied_volatility: float,
    option_type: str,
    risk_free_rate: float = 0.045,
) -> dict | None:
    """Returns {delta, theta} (theta as an estimated $ change per day, per
    share - multiply by 100 for a standard contract). Returns None on any
    invalid input (missing/non-positive IV, DTE, spot, or strike) - the
    caller must skip the contract rather than substitute a fabricated
    value."""
    if not (is_valid(spot_price) and is_valid(strike) and is_valid(implied_volatility)):
        return None
    if spot_price <= 0 or strike <= 0:
        return None
    if not days_to_expiration or days_to_expiration <= 0 or implied_volatility <= 0:
        return None
    if option_type not in ("call", "put"):
        return None

    t = days_to_expiration / 365.0
    sqrt_t = math.sqrt(t)
    d1 = (
        math.log(spot_price / strike) + (risk_free_rate + 0.5 * implied_volatility**2) * t
    ) / (implied_volatility * sqrt_t)
    d2 = d1 - implied_volatility * sqrt_t
    pdf_d1 = _norm_pdf(d1)

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (
            -(spot_price * pdf_d1 * implied_volatility) / (2 * sqrt_t)
            - risk_free_rate * strike * math.exp(-risk_free_rate * t) * _norm_cdf(d2)
        ) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (
            -(spot_price * pdf_d1 * implied_volatility) / (2 * sqrt_t)
            + risk_free_rate * strike * math.exp(-risk_free_rate * t) * _norm_cdf(-d2)
        ) / 365.0

    return {"delta": delta, "theta": theta}
