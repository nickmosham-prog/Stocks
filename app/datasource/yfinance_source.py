"""yfinance-backed implementation of the DataSource interface.

yfinance is an unofficial wrapper around Yahoo Finance endpoints - it has no
SLA and can be rate-limited or change shape without notice. All calls here
are wrapped with retry/backoff, and callers should treat a `None`/empty
return as "temporarily unavailable", not "no data exists".
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from app.config import load_settings
from app.datasource.base import DataSource

log = logging.getLogger(__name__)


def _retry_settings():
    settings = load_settings()
    return (
        settings.get("enrichment", "max_retries", default=3),
        settings.get("enrichment", "retry_backoff_seconds", default=2),
    )


def _with_retry(fn, *args, **kwargs):
    max_retries, backoff = _retry_settings()
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # yfinance can raise many different things
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(backoff * (2**attempt))
    log.warning("data fetch failed after %d attempts: %s", max_retries, last_exc)
    return None


def _num(value, default: float | None = 0.0) -> float | None:
    """float(value), or `default` when missing/NaN. `float(x or 0)` is not
    enough: yfinance reports NaN volume for contracts that haven't traded
    today, and NaN is truthy, so it would slip straight through."""
    try:
        return float(value) if pd.notna(value) else default
    except (TypeError, ValueError):
        return default


def _parse_chain_rows(df: pd.DataFrame, expiration: str, option_type: str) -> list[dict]:
    contracts = []
    for _, row in df.iterrows():
        strike = _num(row.get("strike"), None)
        if not strike:
            continue
        bid, ask = _num(row.get("bid"), None), _num(row.get("ask"), None)
        contracts.append(
            {
                "expiration": expiration,
                "option_type": option_type,
                "strike": strike,
                "volume": _num(row.get("volume")),
                "open_interest": _num(row.get("openInterest")),
                "last_price": _num(row.get("lastPrice")),
                "bid": bid or None,
                "ask": ask or None,
                "contract_symbol": row.get("contractSymbol", ""),
                "implied_volatility": _num(row.get("impliedVolatility"), None),
            }
        )
    return contracts


def _earnings_date(info: dict) -> str | None:
    """Next earnings date (YYYY-MM-DD) from Ticker.info's epoch-seconds
    fields, or None. Past dates are kept as-is - callers compare against
    today themselves."""
    for key in ("earningsTimestampStart", "earningsTimestamp"):
        value = info.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
    return None


class YFinanceSource(DataSource):
    def get_bulk_intraday_bars(
        self, symbols: list[str], period: str, interval: str, prepost: bool
    ) -> dict[str, pd.DataFrame]:
        if not symbols:
            return {}
        settings = load_settings()
        batch_size = settings.get("data_fetch", "batch_size", default=50)
        results: dict[str, pd.DataFrame] = {}

        for i in range(0, len(symbols), batch_size):
            chunk = symbols[i : i + batch_size]
            df = _with_retry(
                yf.download,
                tickers=chunk,
                period=period,
                interval=interval,
                prepost=prepost,
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=False,
            )
            if df is None or df.empty:
                continue
            if len(chunk) == 1:
                symbol = chunk[0]
                sub = df.dropna(how="all")
                if not sub.empty:
                    results[symbol] = sub
                continue
            for symbol in chunk:
                if symbol not in df.columns.get_level_values(0):
                    continue
                sub = df[symbol].dropna(how="all")
                if not sub.empty:
                    results[symbol] = sub
        return results

    def get_profile_history(self, symbol: str, days: int, interval: str) -> pd.DataFrame | None:
        ticker = yf.Ticker(symbol)
        df = _with_retry(
            ticker.history, period=f"{days}d", interval=interval, prepost=True, auto_adjust=False
        )
        if df is None or df.empty:
            return None
        return df

    def get_daily_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        ticker = yf.Ticker(symbol)
        # An explicit start date rather than period=f"{days}d": yfinance only
        # reliably accepts its fixed period strings (1mo, 1y, ...), and the
        # trend metrics need an arbitrary ~400-day window.
        start = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        df = _with_retry(ticker.history, start=start, interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return None
        return df

    def get_news(self, symbol: str) -> list[dict]:
        ticker = yf.Ticker(symbol)
        raw = _with_retry(lambda: ticker.news)
        if not raw:
            return []
        items = []
        for entry in raw:
            # Newer yfinance versions nest fields under "content"; older
            # versions put them at the top level. Support both.
            content = entry.get("content", entry)
            title = content.get("title") or content.get("headline")
            if not title:
                continue
            link = (
                content.get("canonicalUrl", {}).get("url")
                if isinstance(content.get("canonicalUrl"), dict)
                else content.get("link")
            )
            publisher = None
            provider = content.get("provider")
            if isinstance(provider, dict):
                publisher = provider.get("displayName")
            publisher = publisher or content.get("publisher")
            pub_date = content.get("pubDate") or content.get("providerPublishTime")
            published_at = _normalize_published(pub_date)
            items.append(
                {
                    "headline": title,
                    "publisher": publisher,
                    "link": link or f"https://finance.yahoo.com/quote/{symbol}",
                    "published_at": published_at,
                }
            )
        return items

    def get_option_expirations(self, symbol: str) -> list[str] | None:
        ticker = yf.Ticker(symbol)
        expirations = _with_retry(lambda: ticker.options)
        return list(expirations) if expirations else None

    def get_option_chain(self, symbol: str, expirations: list[str] | None = None) -> list[dict] | None:
        ticker = yf.Ticker(symbol)
        if expirations is None:
            all_expirations = _with_retry(lambda: ticker.options)
            if not all_expirations:
                return None
            # Only the nearest 2 expirations by default - keeps this cheap
            # and focused on near-term "smart money" positioning rather
            # than far-dated LEAPS.
            expirations = all_expirations[:2]
        elif not expirations:
            return None

        contracts = []
        for expiration in expirations:
            chain = _with_retry(ticker.option_chain, expiration)
            if chain is None:
                continue
            for option_type, df in (("call", chain.calls), ("put", chain.puts)):
                if df is None or df.empty:
                    continue
                contracts.extend(_parse_chain_rows(df, expiration, option_type))
        return contracts or None

    def get_fundamentals(self, symbol: str) -> dict | None:
        ticker = yf.Ticker(symbol)
        info = _with_retry(lambda: ticker.info)
        if not info:
            return None
        revenue_growth = info.get("revenueGrowth")
        if revenue_growth is None:
            revenue_growth = self._revenue_growth_from_quarterly(ticker)
        return {
            "net_income": info.get("netIncomeToCommon"),
            "trailing_eps": info.get("trailingEps"),
            "trailing_pe": info.get("trailingPE"),
            "revenue_growth_yoy": revenue_growth,
            "profit_margin": info.get("profitMargins"),
            "target_mean_price": info.get("targetMeanPrice"),
            "recommendation_mean": info.get("recommendationMean"),
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "next_earnings_date": _earnings_date(info),
        }

    def _revenue_growth_from_quarterly(self, ticker) -> float | None:
        df = _with_retry(lambda: ticker.quarterly_income_stmt)
        try:
            if df is None or df.empty or "Total Revenue" not in df.index:
                return None
            revenues = df.loc["Total Revenue"].dropna()
            if len(revenues) < 5:  # need same-quarter-last-year, 4 back
                return None
            latest, year_ago = revenues.iloc[0], revenues.iloc[4]
            if not year_ago:
                return None
            return (latest - year_ago) / abs(year_ago)
        except Exception:
            log.warning("could not compute quarterly revenue growth for %s", ticker.ticker)
            return None


def _normalize_published(value) -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        # ISO-8601 string, e.g. "2024-05-01T12:34:56Z"
        return pd.to_datetime(value, utc=True).isoformat()
    except Exception:
        return None
