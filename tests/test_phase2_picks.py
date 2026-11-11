"""Exit plan, market backdrop, sector limit, earnings-soon filter and the
expensive-options flag."""

import math
from datetime import date

import pandas as pd
import pytest

from app import alerts
from app.config import Settings
from app.scan import exit_plan, fundamentals, market_regime, picks, trend

TODAY = date(2026, 11, 10)


def _settings(**picks_overrides):
    return Settings(raw={"scoring": {"fundamentals": {"max_pe": 60}}, "picks": picks_overrides})


def _row(**overrides):
    row = {"symbol": "GOOD", "price": 105.0, "gap_pct": 3.0, "rvol": 2.5, "fundamentals_status": "pass"}
    row.update(overrides)
    return row


TREND = {"sma50": 100.0, "sma200": 90.0, "pct_from_high": -3.0, "rs_3m_pct": 10.0, "hv30": 0.55}
CONTRACT = {
    "option_type": "call", "strike": 100.0, "expiration": "2026-12-18", "price": 6.0, "delta": 0.65,
    "implied_volatility": 0.93, "open_interest": 5000,
}


# --- exit plan ---------------------------------------------------------------

def test_exit_plan_levels_and_option_rules():
    plan = exit_plan.build_exit_plan(100.0, 2.0, CONTRACT, _settings())
    assert plan["stop_price"] == 97.0      # 100 - 1.5 x ATR
    assert plan["target_price"] == 106.0   # 100 + 3 x ATR -> 2:1 reward to risk
    text = "\n".join(plan["lines"])
    assert "Stop: $97.00 (-3.0%)" in text
    assert "Target: $106.00 (+6.0%)" in text
    assert "take profit near $9.00 (+50%), cut the loss near $3.60 (-40%)" in text
    # delta estimate: 6 + 0.65 x (97 - 100) = 4.05 ; 6 + 0.65 x (106 - 100) = 9.90
    assert "about $4.05 at the stop and $9.90 at the target" in text
    assert "Exit the option by 2026-12-04" in text  # 14 days before expiration


def test_exit_plan_respects_settings():
    settings = _settings(exit_plan={"stop_atr_multiple": 2.0, "target_atr_multiple": 4.0})
    plan = exit_plan.build_exit_plan(100.0, 2.0, None, settings)
    assert (plan["stop_price"], plan["target_price"]) == (96.0, 108.0)
    assert len(plan["lines"]) == 2  # no contract -> stock levels only


def test_exit_plan_without_atr_still_gives_option_rules():
    plan = exit_plan.build_exit_plan(100.0, None, CONTRACT, _settings())
    assert plan["stop_price"] is None and plan["target_price"] is None
    assert any(line.startswith("Option: take profit") for line in plan["lines"])
    assert not any("Rough estimate" in line for line in plan["lines"])


def test_exit_plan_with_nothing_usable_is_empty():
    assert exit_plan.build_exit_plan(None, float("nan"), None, _settings())["lines"] == []


# --- market backdrop ---------------------------------------------------------

SPY_TREND = {"sma50": 500.0, "sma200": 480.0}


@pytest.mark.parametrize(
    "price, change, expected",
    [
        (510.0, 0.3, "healthy"),
        (495.0, 0.2, "caution"),   # below the 50-day
        (510.0, -1.4, "caution"),  # above trend but falling hard today
        (495.0, -1.4, "weak"),     # below the 50-day and falling
        (470.0, 0.5, "weak"),      # below the 200-day
    ],
)
def test_market_regime_cases(price, change, expected):
    result = market_regime.assess({"price": price, "gap_pct": change}, SPY_TREND, settings=_settings())
    assert result["regime"] == expected
    assert "S&P 500" in result["note"]


def test_market_regime_note_wording():
    note = market_regime.assess(
        {"price": 495.0, "gap_pct": -1.4}, SPY_TREND, {"gap_pct": -2.1}, _settings()
    )["note"]
    assert note == (
        "Weak market - fewer picks and a higher bar: S&P 500 is below its 50-day average "
        "and down 1.4% today; Nasdaq 100 -2.1%."
    )


def test_market_regime_unknown_without_data_or_when_disabled():
    assert market_regime.assess(None, None, settings=_settings())["regime"] == "unknown"
    disabled = _settings(market_regime={"enabled": False})
    assert market_regime.assess({"price": 470.0, "gap_pct": -3.0}, SPY_TREND, settings=disabled)["regime"] == "unknown"


def test_only_a_weak_market_tightens_the_limits():
    assert market_regime.adjust_limits("healthy", 5, 55, _settings()) == (5, 55)
    assert market_regime.adjust_limits("caution", 5, 55, _settings()) == (5, 55)
    assert market_regime.adjust_limits("weak", 5, 55, _settings()) == (3, 65)


def test_market_warning_appears_in_a_picks_risks():
    market = {"regime": "caution", "note": "Market caution: S&P 500 is below its 50-day average."}
    _, risks = picks.build_thesis(_row(), {}, TREND, None, today=TODAY, settings=_settings(), market=market)
    assert risks[0] == market["note"]
    _, risks = picks.build_thesis(
        _row(), {}, TREND, None, today=TODAY, settings=_settings(), market={"regime": "healthy", "note": "fine"}
    )
    assert "fine" not in risks


# --- sector limit ------------------------------------------------------------

def _candidate(symbol, score, sector):
    return {"symbol": symbol, "eligible": True, "pick_score": score, "sector": sector}


def test_sector_limit_skips_to_the_next_best_idea_elsewhere():
    candidates = [
        _candidate("NVDA", 90, "Technology"), _candidate("AMD", 88, "Technology"),
        _candidate("AVGO", 86, "Technology"), _candidate("LLY", 80, "Healthcare"),
        _candidate("XYZ", 78, None), _candidate("ABC", 76, None), _candidate("DEF", 74, None),
    ]
    chosen = [c["symbol"] for c in picks.rank_picks(candidates, count=5, min_score=55, max_per_sector=2)]
    assert chosen == ["NVDA", "AMD", "LLY", "XYZ", "ABC"]  # AVGO skipped; unknown sectors never limited
    unlimited = [c["symbol"] for c in picks.rank_picks(candidates, count=3, min_score=55, max_per_sector=0)]
    assert unlimited == ["NVDA", "AMD", "AVGO"]


# --- earnings-soon filter ----------------------------------------------------

@pytest.mark.parametrize(
    "earnings, eligible",
    [
        ("2026-11-10", False),  # today
        ("2026-11-13", False),  # 3 days out
        ("2026-11-14", True),   # 4 days out
        ("2026-11-03", True),   # already past (stale date)
        (None, True),           # unknown
    ],
)
def test_earnings_within_three_days_makes_a_stock_ineligible(earnings, eligible):
    fund = {"next_earnings_date": earnings}
    assert picks.is_eligible(_row(), TREND, fund, today=TODAY, settings=_settings()) is eligible


def test_earnings_filter_can_be_turned_off():
    settings = _settings(skip_if_earnings_within_days=-1)
    assert picks.is_eligible(_row(), TREND, {"next_earnings_date": "2026-11-10"}, today=TODAY, settings=settings)


# --- expensive options -------------------------------------------------------

def test_expensive_options_thresholds():
    s = _settings()
    assert picks.expensive_options({"implied_volatility": 0.93}, {"hv30": 0.55}, s) == (0.93, 0.55)
    assert picks.expensive_options({"implied_volatility": 0.40}, {"hv30": 0.35}, s) is None  # only 1.14x
    assert picks.expensive_options({"implied_volatility": 0.13}, {"hv30": 0.08}, s) is None  # 1.6x but only 5 pts
    assert picks.expensive_options({"implied_volatility": None}, {"hv30": 0.5}, s) is None
    assert picks.expensive_options(CONTRACT, {}, s) is None


def test_expensive_options_risk_line():
    _, risks = picks.build_thesis(_row(), {}, TREND, CONTRACT, today=TODAY, settings=_settings())
    assert any(r.startswith("Options are expensive: priced for 93% yearly swings") and "about 55%" in r for r in risks)


def test_realized_volatility():
    # Alternating +1% / -1% daily log returns: sample stdev is ~0.01, annualized x sqrt(252).
    prices, price = [], 100.0
    for i in range(41):
        prices.append(price)
        price *= math.exp(0.01 if i % 2 == 0 else -0.01)
    hv = trend.realized_volatility(pd.Series(prices))
    assert hv == pytest.approx(0.01 * math.sqrt(252), rel=0.03)
    assert trend.realized_volatility(pd.Series([100.0] * 10)) is None  # too little history
    assert trend.compute_trend(pd.Series(prices))["hv30"] == hv


# --- digest ------------------------------------------------------------------

def test_digest_shows_market_note_and_exit_plan():
    pick = {
        "rank": 1, "symbol": "GOOD", "price": 105.0, "pick_score": 78.0, "reasons": "Up 3.0% today", "risks": "",
        "contract_symbol": None, "strike": None, "option_price": None, "contract_note": None,
        "exit_plan": "Stop: $97.00 (-3.0%) - get out if the stock closes below this\nTarget: $106.00 (+6.0%)",
    }
    run = {"scan_ts": "2026-11-10T09:58:00-05:00", "candidates": 4,
           "market_note": "Market caution: S&P 500 is below its 50-day average."}
    _, body = alerts._format_picks_digest("10:00", [pick], run)
    assert "Market caution: S&P 500 is below its 50-day average." in body
    assert "Exit plan (decide before you enter):\n   - Stop: $97.00 (-3.0%)" in body
    _, empty_body = alerts._format_picks_digest("10:00", [], run)
    assert empty_body.startswith("Market caution")


# --- sector backfill ---------------------------------------------------------

def test_rows_without_any_sector_trigger_a_refetch(temp_db):
    with temp_db.cursor() as cur:
        cur.execute(
            "INSERT INTO fundamentals (symbol, profit_margin, fundamentals_status, fetched_at) "
            "VALUES ('AAPL', 0.25, 'pass', '2026-11-10T12:00:00+00:00')"
        )
    assert fundamentals._missing_picks_fields() is True
    with temp_db.cursor() as cur:
        cur.execute("UPDATE fundamentals SET sector = 'Technology'")
    assert fundamentals._missing_picks_fields() is False
