"""Shared numeric guard used anywhere a computed value crosses from "raw
calculation" into something stored, compared, or combined with others.

A stray NaN is dangerous specifically because `float('nan') is not None`
is True - every `if x is not None:` guard in this codebase (and most
Python code) silently treats NaN as "present" and lets it flow into sums
and averages, where it poisons the whole result (NaN + anything = NaN).
Comparisons are just as treacherous: `nan < 80` is False, so a threshold
check like `if score < threshold: skip` never skips a NaN score - it always
looks like it "passed". Route every value through `clean()` at the first
point it could be NaN, and every combination point (sums/averages/threshold
checks) becomes correct by construction.
"""

from __future__ import annotations

import math


def is_valid(x) -> bool:
    """True if x is a usable number: not None, not NaN, not +/-inf."""
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def clean(x):
    """Returns x unchanged if it's a valid finite number, else None."""
    return x if is_valid(x) else None
