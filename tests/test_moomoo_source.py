import threading
import time
from datetime import date, timedelta

import pandas as pd
import pytest

from app.config import Settings
from app.datasource import composite
from app.datasource.composite import CompositeSource, create_source, options_provider_label
from app.datasource.moomoo_source import MoomooOptionsClient, normalize_snapshot
from app.scan import options_strategy

EXP = (date.today() + timedelta(days=35)).isoformat()
EXP_FAR = (date.today() + timedelta(days=40)).isoformat()
NAN = float("nan")


def _snapshot_row(code, option_type="CALL", strike=95.0, expiration=EXP, iv=32.0, delta=0.66, theta=-0.05,
                  oi=800, volume=NAN, last=7.1, bid=7.0, ask=7.2):
    return {
        "code": code, "option_type": option_type, "strike_time": expiration, "option_strike_price": strike,
        "option_open_interest": oi, "option_implied_volatility": iv, "option_delta": delta, "option_theta": theta,
        "volume": volume, "last_price": last, "bid_price": bid, "ask_price": ask,
    }


class FakeContext:
    """Mimics moomoo.OpenQuoteContext's (ret, DataFrame) API."""

    def __init__(self, n_contracts=3, fail=None):
        self.calls = []
        self.snapshot_batches = []
        self.fail = fail or {}
        self.rows = {f"US.AAPL{i}": _snapshot_row(f"US.AAPL{i}", strike=90.0 + i * 5) for i in range(n_contracts)}

    def _maybe_fail(self, name):
        if name in self.fail:
            outcome = self.fail[name]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return None

    def get_global_state(self):
        return self._maybe_fail("get_global_state") or (0, {"qot_logined": True})

    def get_option_expiration_date(self, code):
        self.calls.append(("expirations", code))
        return self._maybe_fail("get_option_expiration_date") or (
            0, pd.DataFrame({"strike_time": [EXP, EXP_FAR], "option_expiry_date_distance": [35, 40]})
        )

    def get_option_chain(self, code, start=None, end=None):
        self.calls.append(("chain", code, start, end))
        rows = [{"code": c, "strike_time": r["strike_time"], "option_type": r["option_type"],
                 "strike_price": r["option_strike_price"]} for c, r in self.rows.items()]
        rows.append({"code": "US.OTHER", "strike_time": "2030-01-18", "option_type": "CALL", "strike_price": 100})
        return self._maybe_fail("get_option_chain") or (0, pd.DataFrame(rows))

    def get_market_snapshot(self, code_list):
        self.snapshot_batches.append(list(code_list))
        return self._maybe_fail("get_market_snapshot") or (
            0, pd.DataFrame([self.rows[c] for c in code_list if c in self.rows])
        )

    def close(self):
        pass


def _client(ctx, **kw):
    return MoomooOptionsClient(context_factory=lambda: ctx, **kw)


# --- normalization ----------------------------------------------------------

def test_normalize_converts_units_and_nans():
    [c] = normalize_snapshot(pd.DataFrame([_snapshot_row("US.AAPL1", option_type="PUT", iv=45.5)]))
    assert c["option_type"] == "put"
    assert c["implied_volatility"] == pytest.approx(0.455)  # percent -> fraction
    assert c["volume"] == 0.0  # NaN volume (untraded today) -> 0, not NaN
    assert c["open_interest"] == 800
    assert c["provider_delta"] == 0.66 and c["provider_theta"] == -0.05
    assert c["ask"] == 7.2 and c["expiration"] == EXP


def test_normalize_keeps_fractional_iv_and_skips_bad_rows():
    rows = [
        _snapshot_row("A", iv=0.4),
        _snapshot_row("B", option_type="STOCK"),
        _snapshot_row("C", strike=NAN),
        _snapshot_row("D", iv=0),
    ]
    contracts = normalize_snapshot(pd.DataFrame(rows))
    assert [c["contract_symbol"] for c in contracts] == ["A", "D"]
    assert contracts[0]["implied_volatility"] == 0.4
    assert contracts[1]["implied_volatility"] is None


# --- client -----------------------------------------------------------------

def test_client_expirations_and_chain_filtered_to_requested_window():
    ctx = FakeContext()
    client = _client(ctx)
    assert client.get_option_expirations("aapl") == [EXP, EXP_FAR]
    chain = client.get_option_chain("aapl", [EXP])
    assert {c["contract_symbol"] for c in chain} == set(ctx.rows)  # US.OTHER (2030) filtered out
    assert ("chain", "US.AAPL", EXP, EXP) in ctx.calls


def test_snapshot_requests_are_batched():
    ctx = FakeContext(n_contracts=450)
    chain = _client(ctx).get_option_chain("AAPL", [EXP])
    assert len(chain) == 450
    assert [len(b) for b in ctx.snapshot_batches] == [200, 200, 50]


def test_sdk_error_returns_none():
    ctx = FakeContext(fail={"get_option_expiration_date": (-1, "no quote right")})
    assert _client(ctx).get_option_expirations("AAPL") is None


def test_missing_snapshot_columns_returns_none():
    ctx = FakeContext()
    ctx.get_market_snapshot = lambda codes: (0, pd.DataFrame([{"code": c} for c in codes]))
    assert _client(ctx).get_option_chain("AAPL", [EXP]) is None


def test_hung_call_times_out_and_backs_off():
    ctx = FakeContext()
    release = threading.Event()

    def hang(code):
        release.wait(5)
        return 0, pd.DataFrame()

    ctx.get_option_expiration_date = hang
    client = _client(ctx, call_timeout=0.2, retry_after=60)
    started = time.monotonic()
    assert client.get_option_expirations("AAPL") is None
    assert time.monotonic() - started < 2
    # Backing off: the next call returns immediately without touching OpenD.
    ctx.calls.clear()
    assert client.get_option_chain("AAPL", [EXP]) is None
    assert ctx.calls == []
    release.set()


def test_connection_error_backs_off_then_recovers(monkeypatch):
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("OpenD is not running")
        return FakeContext()

    client = MoomooOptionsClient(context_factory=factory, retry_after=0.05)
    assert client.get_option_expirations("AAPL") is None
    time.sleep(0.1)
    assert client.get_option_expirations("AAPL") == [EXP, EXP_FAR]


def test_check_connection():
    assert _client(FakeContext()).check_connection()[0] is True
    bad = FakeContext(fail={"get_global_state": (-1, "not logged in")})
    ok, message = _client(bad).check_connection()
    assert ok is False and "not logged in" in message


# --- composite --------------------------------------------------------------

class FakeYahoo:
    def __init__(self):
        self.calls = []

    def get_option_expirations(self, symbol):
        self.calls.append("expirations")
        return ["yahoo-exp"]

    def get_option_chain(self, symbol, expirations=None):
        self.calls.append(("chain", expirations))
        return [{"source": "yahoo"}]


def test_composite_prefers_moomoo_for_option_ideas():
    yahoo, ctx = FakeYahoo(), FakeContext()
    source = CompositeSource(yahoo, _client(ctx))
    assert source.get_option_expirations("AAPL") == [EXP, EXP_FAR]
    chain = source.get_option_chain("AAPL", expirations=[EXP])
    assert chain and "provider_delta" in chain[0]
    assert yahoo.calls == []
    assert options_provider_label(source) == "moomoo"


def test_composite_options_flow_scan_stays_on_yahoo():
    yahoo, ctx = FakeYahoo(), FakeContext()
    source = CompositeSource(yahoo, _client(ctx))
    assert source.get_option_chain("AAPL") == [{"source": "yahoo"}]
    assert ctx.calls == []


def test_composite_falls_back_to_yahoo():
    yahoo = FakeYahoo()
    ctx = FakeContext(fail={"get_option_expiration_date": RuntimeError("boom"), "get_option_chain": (-1, "err")})
    source = CompositeSource(yahoo, _client(ctx))
    assert source.get_option_expirations("AAPL") == ["yahoo-exp"]
    assert source.get_option_chain("AAPL", expirations=[EXP]) == [{"source": "yahoo"}]
    assert options_provider_label(source) == "yahoo (moomoo unavailable)"


def test_composite_falls_back_when_sdk_not_installed():
    def factory():
        raise ImportError("No module named 'moomoo'")

    source = CompositeSource(FakeYahoo(), MoomooOptionsClient(context_factory=factory))
    assert source.get_option_expirations("AAPL") == ["yahoo-exp"]


def test_factory_respects_settings():
    base = object()
    assert create_source(Settings(raw={}), base=base) is base
    assert create_source(Settings(raw={"options_source": {"provider": "yahoo"}}), base=base) is base
    src = create_source(Settings(raw={"options_source": {"provider": "moomoo", "moomoo": {"port": 11112}}}), base=base)
    assert isinstance(src, CompositeSource) and src.options_client._port == 11112
    assert options_provider_label(base) == "yahoo"


# --- selector uses the provider's Greeks -------------------------------------

def _strategy_settings():
    return Settings(raw={"scoring": {"options_strategy": {
        "dte_min": 30, "dte_max": 45, "target_delta_call": 0.65, "target_delta_put": -0.65,
        "delta_band": 0.10, "risk_free_rate": 0.045, "min_open_interest": 50, "min_volume": 10,
    }}})


def test_selector_uses_provider_delta_over_black_scholes():
    # Black-Scholes would put a 95 strike at ~0.74 delta; the provider says 0.64.
    contract = normalize_snapshot(pd.DataFrame([_snapshot_row("X", strike=95.0, delta=0.64, theta=-0.08)]))[0]
    result = options_strategy.select_contract([contract], 100.0, "bullish", settings=_strategy_settings())
    assert result["delta"] == 0.64 and result["theta"] == -0.08


def test_selector_falls_back_to_black_scholes_without_provider_delta():
    contract = normalize_snapshot(pd.DataFrame([_snapshot_row("X", strike=95.0, delta=NAN, theta=NAN, iv=30.0)]))[0]
    result = options_strategy.select_contract([contract], 100.0, "bullish", settings=_strategy_settings())
    assert result is not None and 0.55 <= result["delta"] <= 0.75 and result["theta"] < 0


def test_unsigned_put_delta_is_corrected():
    contract = normalize_snapshot(pd.DataFrame([_snapshot_row("P", option_type="PUT", strike=105.0, delta=0.66)]))[0]
    result = options_strategy.select_contract([contract], 100.0, "bearish", settings=_strategy_settings())
    assert result["delta"] == -0.66


# --- diagnostic tool ---------------------------------------------------------

def test_check_moomoo_happy_path(monkeypatch, capsys):
    from app.tools import check_moomoo

    ctx = FakeContext()
    monkeypatch.setattr(check_moomoo, "MoomooOptionsClient", lambda host, port: _client(ctx))

    class _Yahoo:
        def get_daily_history(self, symbol, days):
            return pd.DataFrame({"Close": [99.0, 100.0]})

        def get_option_expirations(self, symbol):
            return None

        def get_option_chain(self, symbol, expirations=None):
            return None

    monkeypatch.setattr(check_moomoo, "YFinanceSource", _Yahoo)
    assert check_moomoo.main("AAPL") == 0
    out = capsys.readouterr().out
    assert "OK: connected to OpenD" in out
    assert "option_implied_volatility" in out
    assert "moomoo: CALL $" in out and "delta 0.66" in out
    assert "yahoo : no contract (No option expirations listed)" in out
