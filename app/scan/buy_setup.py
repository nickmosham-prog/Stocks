"""Single source of truth for "does this row meet the BUY Setup criteria" -
used both to decide whether to email about it (app/alerts.py) and whether
to show the BUY badge on the dashboard (app/scan/runner.py), so the two
can never disagree with each other.
"""

from __future__ import annotations

from app.config import load_settings
from app.scan.numeric import is_valid


def meets_buy_setup_criteria(row: dict, settings=None) -> bool:
    settings = settings or load_settings()
    buy_cfg = settings.get("alerts", "buy_setup", default={})
    if not buy_cfg.get("enabled", False):
        return False

    alpha = row.get("alpha_score")
    if not is_valid(alpha) or alpha < buy_cfg.get("alpha_score_threshold", 88):
        return False

    if buy_cfg.get("require_confirmed_breakout", True) and not row.get("breakout_confirmed"):
        return False

    if buy_cfg.get("require_bullish_direction", True) and row.get("breakout_direction") != "bullish":
        return False

    return True
