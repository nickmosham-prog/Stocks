"""Options 'unusual activity' proxy from free chain data (volume/open-interest
ratio + call/put skew). Not a real options-flow/sweep feed - see README.
"""

from __future__ import annotations

import math
import statistics

from app.config import load_settings


def _average_iv(contracts: list[dict]) -> float | None:
    ivs = [c["implied_volatility"] for c in contracts if c.get("implied_volatility") is not None]
    return statistics.mean(ivs) if ivs else None


def compute_options_activity(contracts: list[dict] | None) -> dict:
    """Returns {options_score, call_put_ratio, max_vol_oi_ratio,
    avg_implied_volatility, flagged_contracts}.

    options_score is None when the symbol has no listed options (excluded
    from the alpha score entirely, not treated as zero). Implied volatility
    is informational context (options are "expensive" or "cheap" right now)
    - it does not feed into the numeric score.
    """
    if not contracts:
        return {
            "options_score": None,
            "call_put_ratio": None,
            "max_vol_oi_ratio": None,
            "avg_implied_volatility": None,
            "flagged_contracts": [],
        }

    settings = load_settings()
    cfg = settings.get("scoring", "options", default={})
    oi_cap = cfg.get("vol_oi_cap", 5.0)
    skew_cap = cfg.get("skew_cap_ln", math.log(3))
    min_volume = cfg.get("min_contract_volume", 100)
    weights = cfg.get("weights", {"vol_oi": 0.70, "skew": 0.30})

    total_call_volume = sum(c["volume"] for c in contracts if c["option_type"] == "call")
    total_put_volume = sum(c["volume"] for c in contracts if c["option_type"] == "put")
    call_put_ratio = total_call_volume / max(total_put_volume, 1.0)

    flagged = []
    for c in contracts:
        ratio = c["volume"] / max(c["open_interest"], 1.0)
        if c["volume"] >= min_volume and ratio > 1.0:
            flagged.append({**c, "vol_oi_ratio": ratio})

    if flagged:
        max_vol_oi_ratio = max(c["vol_oi_ratio"] for c in flagged)
    else:
        # still report the best ratio even if nothing cleared the noise
        # filter, so the score reflects "nothing unusual" rather than
        # crashing on an empty max().
        max_vol_oi_ratio = max((c["volume"] / max(c["open_interest"], 1.0) for c in contracts), default=0.0)

    skew_component = min(abs(math.log(call_put_ratio)) / skew_cap, 1.0) if call_put_ratio > 0 else 0.0
    options_score = (
        weights.get("vol_oi", 0.70) * min(max_vol_oi_ratio / oi_cap, 1.0) * 100.0
        + weights.get("skew", 0.30) * skew_component * 100.0
    )

    # Prefer IV of the contracts that actually triggered the flag (most
    # relevant to "what would it cost to trade the unusual activity right
    # now"); fall back to the whole chain if nothing was flagged.
    avg_iv = _average_iv(flagged) if flagged else _average_iv(contracts)

    return {
        "options_score": options_score,
        "call_put_ratio": call_put_ratio,
        "max_vol_oi_ratio": max_vol_oi_ratio,
        "avg_implied_volatility": avg_iv,
        "flagged_contracts": sorted(flagged, key=lambda c: c["vol_oi_ratio"], reverse=True)[:20],
    }
