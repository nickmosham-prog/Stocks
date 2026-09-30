"""Moomoo OpenAPI client for option chains with exchange-grade Greeks/IV.

Needs moomoo's "OpenD" gateway app running (and logged in) on the same
machine; see README "Using moomoo for options data". Every method returns
None on any problem - OpenD not running, SDK not installed, missing quote
rights, unexpected columns - so the caller (app/datasource/composite.py)
can fall back to Yahoo. It never raises into a scan.
"""

from __future__ import annotations

import logging
import socket
import threading
import time

import pandas as pd

from app.datasource.yfinance_source import _num

log = logging.getLogger(__name__)

SNAPSHOT_BATCH = 200  # SDK allows up to 400 codes per snapshot call; stay well under
REQUIRED_SNAPSHOT_COLUMNS = {"code", "option_type", "strike_time", "option_strike_price"}


def _moomoo_code(symbol: str) -> str:
    return f"US.{symbol.upper()}"


def _normalize_iv(raw) -> float | None:
    """Moomoo reports IV as a percent (35.2 = 35.2%). A real equity option
    IV under 3% essentially never happens, so anything above 3 is treated
    as a percent and anything at or below as an already-fractional value."""
    iv = _num(raw, None)
    if iv is None or iv <= 0:
        return None
    return iv / 100.0 if iv > 3.0 else iv


def normalize_snapshot(df: pd.DataFrame) -> list[dict]:
    """Snapshot rows -> the contract dicts the rest of the app uses
    (same shape as YFinanceSource.get_option_chain), plus the provider's
    own delta/theta."""
    contracts = []
    for _, row in df.iterrows():
        option_type = str(row.get("option_type", "")).upper()
        if option_type not in ("CALL", "PUT"):
            continue
        strike = _num(row.get("option_strike_price"), None)
        expiration = str(row.get("strike_time", ""))[:10]
        if not strike or len(expiration) != 10:
            continue
        bid, ask = _num(row.get("bid_price"), None), _num(row.get("ask_price"), None)
        contracts.append(
            {
                "expiration": expiration,
                "option_type": option_type.lower(),
                "strike": strike,
                "volume": _num(row.get("volume")),
                "open_interest": _num(row.get("option_open_interest")),
                "last_price": _num(row.get("last_price")),
                "bid": bid or None,
                "ask": ask or None,
                "contract_symbol": str(row.get("code", "")),
                "implied_volatility": _normalize_iv(row.get("option_implied_volatility")),
                "provider_delta": _num(row.get("option_delta"), None),
                "provider_theta": _num(row.get("option_theta"), None),
            }
        )
    return contracts


CALL_TIMEOUT_SECONDS = 15
RETRY_AFTER_SECONDS = 300


def _run_with_timeout(fn, timeout: float):
    """The SDK can block indefinitely when OpenD is closed or stuck (its
    connect loop retries forever), so every call gets a hard deadline."""
    result: dict = {}

    def target():
        try:
            result["value"] = fn()
        except BaseException as exc:  # re-raised in the caller's thread
            result["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"no response within {timeout:.0f}s")
    if "error" in result:
        raise result["error"]
    return result["value"]


class MoomooOptionsClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        context_factory=None,
        call_timeout: float = CALL_TIMEOUT_SECONDS,
        retry_after: float = RETRY_AFTER_SECONDS,
    ):
        self._host, self._port = host, port
        self._context_factory = context_factory  # tests inject a fake
        self._call_timeout, self._retry_after = call_timeout, retry_after
        self._ctx = None
        self._down_until = 0.0
        self._lock = threading.Lock()
        self._warned: set[str] = set()

    def _warn_once(self, key: str, message: str, *args, **kwargs) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message, *args, **kwargs)

    def _port_open(self) -> bool:
        try:
            with socket.create_connection((self._host, self._port), timeout=2):
                return True
        except OSError:
            return False

    def _context(self):
        if self._ctx is None:
            if self._context_factory is not None:
                self._ctx = self._context_factory()
                return self._ctx
            from moomoo import OpenQuoteContext, SysConfig

            if not self._port_open():
                raise ConnectionError(f"OpenD is not running at {self._host}:{self._port}")
            # Daemon threads so Ctrl+C still stops the app; keep the SDK's own
            # chatter out of the terminal (it still logs to its own file).
            SysConfig.set_all_thread_daemon(True)
            SysConfig.enable_console_log(False)
            self._ctx = OpenQuoteContext(host=self._host, port=self._port, is_async_connect=True)
        return self._ctx

    def _mark_down(self, reason: str) -> None:
        if self._down_until <= time.monotonic():
            log.warning(
                "moomoo options data unavailable (%s) - using Yahoo, retrying in %d min",
                reason, self._retry_after // 60,
            )
        self._down_until = time.monotonic() + self._retry_after
        ctx, self._ctx = self._ctx, None
        if ctx is not None:
            # close() can block too if OpenD is stuck; don't wait for it.
            threading.Thread(target=lambda: _safe_close(ctx), daemon=True).start()

    def _invoke(self, name: str, *args, **kwargs):
        """(ret, data) from the SDK. Raises on unavailability/timeout."""
        return _run_with_timeout(lambda: getattr(self._context(), name)(*args, **kwargs), self._call_timeout)

    def _call(self, name: str, *args, **kwargs):
        """Returns the SDK DataFrame, or None on any failure."""
        with self._lock:
            if time.monotonic() < self._down_until:
                return None
            try:
                ret, data = self._invoke(name, *args, **kwargs)
            except ImportError:
                self._warn_once("import", "moomoo-api is not installed - using Yahoo for options (pip install -r requirements.txt)")
                return None
            except (ConnectionError, TimeoutError) as exc:
                self._mark_down(str(exc))
                return None
            except Exception:
                self._warn_once(f"exc-{name}", "moomoo %s failed - using Yahoo for options", name, exc_info=True)
                return None
        if ret != 0:  # moomoo.RET_OK == 0
            self._warn_once(f"ret-{name}-{data}", "moomoo %s returned an error: %s - using Yahoo for options", name, data)
            return None
        return data if isinstance(data, pd.DataFrame) else None

    def check_connection(self) -> tuple[bool, str]:
        with self._lock:
            try:
                ret, data = self._invoke("get_global_state")
            except ImportError:
                return False, "moomoo-api is not installed (run: pip install -r requirements.txt)"
            except (ConnectionError, TimeoutError) as exc:
                self._mark_down(str(exc))
                return False, f"could not reach OpenD at {self._host}:{self._port} ({exc})"
            except Exception as exc:
                return False, f"moomoo error: {exc}"
        if ret != 0:
            return False, f"OpenD at {self._host}:{self._port} answered with an error: {data}"
        return True, "connected to OpenD"

    def get_option_expirations(self, symbol: str) -> list[str] | None:
        df = self._call("get_option_expiration_date", _moomoo_code(symbol))
        if df is None or df.empty or "strike_time" not in df.columns:
            return None
        expirations = sorted({str(v)[:10] for v in df["strike_time"] if pd.notna(v)})
        return expirations or None

    def get_option_chain(self, symbol: str, expirations: list[str]) -> list[dict] | None:
        if not expirations:
            return None
        wanted = set(expirations)
        chain = self._call(
            "get_option_chain", _moomoo_code(symbol), start=min(wanted), end=max(wanted)
        )  # the SDK caps start..end at 30 days; the 30-45 DTE window spans 15
        if chain is None or chain.empty or "code" not in chain.columns:
            return None
        if "strike_time" in chain.columns:
            chain = chain[chain["strike_time"].astype(str).str[:10].isin(wanted)]
        codes = [c for c in chain["code"].tolist() if c]
        if not codes:
            return None

        frames = []
        for i in range(0, len(codes), SNAPSHOT_BATCH):
            snap = self._call("get_market_snapshot", codes[i : i + SNAPSHOT_BATCH])
            if snap is None:
                return None
            frames.append(snap)
        snapshot = pd.concat(frames, ignore_index=True)
        missing = REQUIRED_SNAPSHOT_COLUMNS - set(snapshot.columns)
        if missing:
            self._warn_once("columns", "moomoo snapshot is missing columns %s - using Yahoo for options", sorted(missing))
            return None
        return normalize_snapshot(snapshot) or None

    def close(self) -> None:
        ctx, self._ctx = self._ctx, None
        if ctx is not None:
            try:
                _run_with_timeout(lambda: _safe_close(ctx), 5)
            except TimeoutError:
                pass


def _safe_close(ctx) -> None:
    try:
        ctx.close()
    except Exception:
        pass
