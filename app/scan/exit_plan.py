"""Exit plan for a Top Pick: where to get out if it fails, where to take
profit, and simple rules for the option idea.

Levels are set from ATR14 (the stock's average daily range over 14 days),
so a volatile stock gets a wider stop than a quiet one. These are
rule-of-thumb levels to decide *before* entering - not predictions.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.config import load_settings
from app.scan.numeric import clean, is_valid

DEFAULTS = {
    "stop_atr_multiple": 1.5,
    "target_atr_multiple": 3.0,
    "option_take_profit_pct": 50,
    "option_stop_loss_pct": 40,
    "exit_days_before_expiration": 14,
}


def _expiration_date(contract: dict) -> date | None:
    try:
        return datetime.strptime(str(contract.get("expiration"))[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def build_exit_plan(price: float | None, atr14: float | None, contract: dict | None = None, settings=None) -> dict:
    """Returns {stop_price, target_price, lines}. stop/target are None when
    the stock has no usable ATR; `lines` are plain-English steps."""
    settings = settings or load_settings()
    cfg = {**DEFAULTS, **(settings.get("picks", "exit_plan", default={}) or {})}
    lines: list[str] = []
    stop = target = None

    if is_valid(price) and price > 0 and is_valid(atr14) and atr14 > 0:
        stop = clean(round(max(price - cfg["stop_atr_multiple"] * atr14, 0.01), 2))
        target = clean(round(price + cfg["target_atr_multiple"] * atr14, 2))
        lines.append(
            f"Stop: ${stop:,.2f} ({(stop / price - 1) * 100:+.1f}%) - get out if the stock closes below this"
        )
        lines.append(f"Target: ${target:,.2f} ({(target / price - 1) * 100:+.1f}%) - a sensible place to take profit")

    entry = (contract or {}).get("price")
    if contract and is_valid(entry) and entry > 0:
        take_profit = entry * (1 + cfg["option_take_profit_pct"] / 100.0)
        cut = entry * (1 - cfg["option_stop_loss_pct"] / 100.0)
        lines.append(
            f"Option: take profit near ${take_profit:,.2f} (+{cfg['option_take_profit_pct']:g}%), "
            f"cut the loss near ${cut:,.2f} (-{cfg['option_stop_loss_pct']:g}%)"
        )
        delta = contract.get("delta")
        if stop is not None and is_valid(delta):
            at_stop = max(entry + delta * (stop - price), 0.0)
            at_target = max(entry + delta * (target - price), 0.0)
            lines.append(
                f"Rough estimate: the option is worth about ${at_stop:,.2f} at the stop "
                f"and ${at_target:,.2f} at the target"
            )
        expiration = _expiration_date(contract)
        if expiration:
            exit_by = expiration - timedelta(days=cfg["exit_days_before_expiration"])
            lines.append(
                f"Exit the option by {exit_by.isoformat()} whatever happens - "
                f"time decay speeds up in the last {cfg['exit_days_before_expiration']:g} days"
            )

    return {"stop_price": stop, "target_price": target, "lines": lines}
