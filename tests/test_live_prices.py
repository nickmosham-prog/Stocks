"""Live stock quotes from moomoo: client, composite source and the scan."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.config import Settings
from app.datasource.composite import CompositeSource, create_source, prices_provider_label
from app.datasource.moomoo_source import MoomooOptionsClient
from app.scan import runner

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 10, 10, 30, tzinfo=ET)


def _stock_row(code, last=100.0, high=101.0, low=99.0, pre="N/A"):
    return {"code": code, "last_price": last, "high_price": high, "low_price": low, "prev_close_price": 98.0,
            "volume": 1_000_000, "pre_price": pre, "pre_high_price": pre, "pre_low_price": pre}


class StockContext:
    """Mimics OpenQuoteContext.get_market_snapshot for stocks. Like OpenD,
    one unknown code fails the whole request."""

    def __init__(self, known, fail_everything=False):
        self.known = known  # code -> row
        self.fail_everything = fail_everything
        self.requests = []

    def get_market_snapshot(self, codes):
        self.requests.append(list(codes))
        if self.fail_everything:
            return -1, "request frequency too high"
        unknown = [c for c in codes if c not in self.known]
        if unknown:
            return -1, f"unknown stock {unknown[0]}"
        return 0, pd.DataFrame([self.known[c] for c in codes])

    def get_global_state(self):
        return 0, {}

    def close(self):
        pass


def _client(ctx, **kwargs):
    return MoomooOptionsClient(context_factory=lambda: ctx, **kwargs)


# --- client ------------------------------------------------------------------

def test_stock_snapshots_are_normalized_and_share_classes_mapped():
    ctx = StockContext({
        "US.AAPL": _stock_row("US.AAPL", last=336.6, high=338.0, low=334.1, pre=335.0),
        "US.BRK.B": _stock_row("US.BRK.B", last=480.0),
    })
    quotes = _client(ctx).get_stock_snapshots(["AAPL", "BRK-B"])
    assert quotes["AAPL"] == {"price": 336.6, "high": 338.0, "low": 334.1,
                              "pre_price": 335.0, "pre_high": 335.0, "pre_low": 335.0}
    assert quotes["BRK-B"]["price"] == 480.0          # Yahoo's BRK-B is moomoo's US.BRK.B
    assert quotes["BRK-B"]["pre_price"] is None       # 'N/A' outside the pre-market
    assert ctx.requests == [["US.AAPL", "US.BRK.B"]]  # one request for the whole list


def test_one_unknown_ticker_is_isolated_and_skipped_from_then_on():
    known = {f"US.S{i}": _stock_row(f"US.S{i}") for i in range(7)}
    ctx = StockContext(known)
    client = _client(ctx)
    symbols = [f"S{i}" for i in range(7)] + ["ZZZZ"]
    quotes = client.get_stock_snapshots(symbols)
    assert sorted(quotes) == [f"S{i}" for i in range(7)]
    ctx.requests.clear()
    assert len(client.get_stock_snapshots(symbols)) == 7
    assert ctx.requests == [[f"US.S{i}" for i in range(7)]]  # ZZZZ is no longer asked for


def test_a_general_failure_backs_off_without_blacklisting_the_watchlist():
    ctx = StockContext({}, fail_everything=True)
    client = _client(ctx, retry_after=60)
    symbols = [f"S{i}" for i in range(40)]
    assert client.get_stock_snapshots(symbols) == {}
    assert len(ctx.requests) < 25                    # gave up quickly instead of probing all 40
    assert client._unknown_codes == set()
    ctx.requests.clear()
    assert client.get_stock_snapshots(symbols) == {}  # backing off: no new requests
    assert ctx.requests == []
    assert client._is_down() is False                # the options connection is unaffected


def test_a_short_watchlist_rejected_entirely_is_a_general_failure_too():
    ctx = StockContext({}, fail_everything=True)
    client = _client(ctx, retry_after=60)
    assert client.get_stock_snapshots(["AAPL", "MSFT", "SPY"]) == {}
    assert client._unknown_codes == set()


def test_no_quotes_when_opend_is_not_running():
    def factory():
        raise ConnectionError("OpenD is not running")

    assert MoomooOptionsClient(context_factory=factory).get_stock_snapshots(["AAPL"]) == {}


# --- composite source --------------------------------------------------------

class FakeYahoo:
    def get_option_expirations(self, symbol):
        return ["yahoo-exp"]

    def get_option_chain(self, symbol, expirations=None):
        return [{"source": "yahoo"}]


def test_live_prices_are_off_unless_enabled():
    ctx = StockContext({"US.AAPL": _stock_row("US.AAPL")})
    source = CompositeSource(FakeYahoo(), _client(ctx))  # options only (the default)
    assert source.get_realtime_quotes(["AAPL"]) == {}
    assert ctx.requests == []
    assert prices_provider_label(source) == "yahoo"


def test_live_prices_on_and_status_follows_availability():
    ctx = StockContext({"US.AAPL": _stock_row("US.AAPL")})
    source = CompositeSource(FakeYahoo(), _client(ctx), use_for_options=False, use_for_prices=True)
    assert source.get_realtime_quotes(["AAPL"])["AAPL"]["price"] == 100.0
    assert prices_provider_label(source) == "moomoo"
    # Options were not switched to moomoo, so they go straight to Yahoo.
    assert source.get_option_expirations("AAPL") == ["yahoo-exp"]
    assert source.get_option_chain("AAPL", expirations=["x"]) == [{"source": "yahoo"}]

    ctx.fail_everything = True
    assert source.get_realtime_quotes([f"S{i}" for i in range(10)]) == {}
    assert prices_provider_label(source) == "yahoo (moomoo unavailable)"


def test_factory_reads_both_settings():
    base = FakeYahoo()
    assert create_source(Settings(raw={}), base=base) is base
    prices_only = create_source(Settings(raw={"realtime_prices": {"provider": "moomoo"}}), base=base)
    assert isinstance(prices_only, CompositeSource)
    assert (prices_only.use_for_options, prices_only.use_for_prices) == (False, True)
    both = create_source(
        Settings(raw={"realtime_prices": {"provider": "moomoo"}, "options_source": {"provider": "moomoo"}}), base=base
    )
    assert (both.use_for_options, both.use_for_prices) == (True, True)


# --- the scan ----------------------------------------------------------------

def test_live_levels_by_session():
    quote = {"price": 101.0, "high": 102.0, "low": 99.5, "pre_price": 100.4, "pre_high": 100.9, "pre_low": 99.9}
    assert runner._live_levels(quote, "regular", 100.0) == {"price": 101.0, "high": 102.0, "low": 99.5}
    assert runner._live_levels(quote, "premarket", 100.0) == {"price": 100.4, "high": 100.9, "low": 99.9}
    assert runner._live_levels(None, "regular", 100.0) is None
    assert runner._live_levels({"price": None}, "regular", 100.0) is None
    assert runner._live_levels({"price": 0.0}, "regular", 100.0) is None
    assert runner._live_levels({"pre_price": None, "price": 101.0}, "premarket", 100.0) is None


def test_a_quote_far_from_the_latest_bar_is_ignored():
    assert runner._live_levels({"price": 130.0}, "regular", 100.0) is None   # +30%: bad quote or wrong symbol
    assert runner._live_levels({"price": 110.0}, "regular", 100.0) is not None


def _bars(close=100.0, volume=50_000):
    index = pd.date_range(NOW - timedelta(minutes=25), NOW, freq="5min")
    n = len(index)
    return pd.DataFrame({"Open": [close] * n, "High": [close + 0.2] * n, "Low": [close - 0.2] * n,
                         "Close": [close] * n, "Volume": [volume] * n}, index=index)


def test_live_quote_overrides_price_and_range_but_not_volume(temp_db, monkeypatch):
    monkeypatch.setattr(runner, "now_et", lambda: NOW)
    with temp_db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_ohlc (symbol, trade_date, open, high, low, close, volume, atr14) "
            "VALUES ('AAPL', '2026-11-09', 98, 99, 97, 98, 1000000, 2.0)"
        )
    from_bars = runner._score_symbol("AAPL", _bars(), "regular", 5)
    quote = {"price": 101.5, "high": 102.4, "low": 98.8}
    live = runner._score_symbol("AAPL", _bars(), "regular", 5, quote)

    assert from_bars["price"] == 100.0 and live["price"] == 101.5
    assert live["gap_pct"] == pytest.approx((101.5 / 98 - 1) * 100)     # change measured from the live price
    assert live["range_expansion"] == pytest.approx((102.4 - 98.8) / 2.0)  # live day high/low
    assert live["cum_volume_today"] == from_bars["cum_volume_today"] == 6 * 50_000


def test_status_reports_the_price_source(temp_db):
    from fastapi.testclient import TestClient

    from app.web.server import create_app

    assert TestClient(create_app()).get("/api/status").json()["prices_source"] == "yahoo"
    source = CompositeSource(FakeYahoo(), _client(StockContext({})), use_for_options=False, use_for_prices=True)
    assert TestClient(create_app(source)).get("/api/status").json()["prices_source"] == "moomoo"
