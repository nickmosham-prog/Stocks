"""Bearish counterpart to app/scan/buy_setup.py: "does this row meet the
BREAKDOWN Setup criteria" - a confirmed bearish breakout with a high Alpha
Score. Drives `breakdown_signal` the same way `buy_signal` already works,
and is the trigger for a PUT trade recommendation (app/scan/options_strategy.py).

Deliberately has no fundamentals gate - the fundamentals quality filter in
buy_setup.py is specifically about validating a BUY thesis ("real
financials, not just narratives"); a bearish breakdown candidate is often a
worse-fundamentals company by nature, so applying the same gate here would
be backwards.
"""

from __future__ import annotations

from app.config import load_settings
from app.scan.buy_setup import _meets_technical_criteria


def meets_breakdown_setup_criteria(row: dict, settings=None) -> bool:
    settings = settings or load_settings()
    cfg = settings.get("alerts", "breakdown_setup", default={})
    if not cfg.get("enabled", False):
        return False
    return _meets_technical_criteria(row, cfg, direction="bearish")
