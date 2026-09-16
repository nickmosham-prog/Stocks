"""Single source of truth for "does this row meet the BUY Setup criteria" -
used both to decide whether to email about it (app/alerts.py) and whether
to show the BUY badge on the dashboard (app/scan/runner.py), so the two
can never disagree with each other.

The shared `_meets_technical_criteria()` helper is also used by
app/scan/breakdown_setup.py (the bearish counterpart) so the technical
checks (alpha threshold, confirmed breakout, direction) can never drift
between the two.
"""

from __future__ import annotations

from app.config import load_settings
from app.scan.numeric import is_valid


def _meets_technical_criteria(row: dict, cfg: dict, direction: str) -> bool:
    alpha = row.get("alpha_score")
    if not is_valid(alpha) or alpha < cfg.get("alpha_score_threshold", 88):
        return False

    if cfg.get("require_confirmed_breakout", True) and not row.get("breakout_confirmed"):
        return False

    require_key = "require_bullish_direction" if direction == "bullish" else "require_bearish_direction"
    if cfg.get(require_key, True) and row.get("breakout_direction") != direction:
        return False

    return True


def meets_buy_setup_criteria(row: dict, settings=None) -> bool:
    settings = settings or load_settings()
    buy_cfg = settings.get("alerts", "buy_setup", default={})
    if not buy_cfg.get("enabled", False):
        return False

    if not _meets_technical_criteria(row, buy_cfg, direction="bullish"):
        return False

    # "Real financials, not just narratives": an additional required gate
    # on top of the technical checks above, not a replacement for them.
    # Bullish-only - a bearish breakdown candidate is often a worse-
    # fundamentals company by nature, so the same gate would be backwards
    # for app/scan/breakdown_setup.py.
    if buy_cfg.get("require_fundamentals", True) and row.get("fundamentals_status") != "pass":
        return False

    return True
