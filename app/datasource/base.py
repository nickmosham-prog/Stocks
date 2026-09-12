"""Data-source interface.

Every external market-data call in the app goes through this interface so a
future swap to a paid real-time feed (Polygon, Finnhub, IEX, ...) only
requires a new implementation of this class - nothing in app/scan or
app/web should import yfinance directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataSource(ABC):
    @abstractmethod
    def get_bulk_intraday_bars(
        self, symbols: list[str], period: str, interval: str, prepost: bool
    ) -> dict[str, pd.DataFrame]:
        """Bulk fetch of recent intraday bars for many symbols in one call.

        Returns {symbol: DataFrame} with columns [Open, High, Low, Close, Volume]
        indexed by tz-aware timestamp. Missing/failed symbols are simply absent
        from the returned dict (caller marks them data_stale).
        """

    @abstractmethod
    def get_profile_history(self, symbol: str, days: int, interval: str) -> pd.DataFrame | None:
        """Historical intraday bars for one symbol, used to build the volume profile."""

    @abstractmethod
    def get_daily_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        """Daily OHLCV bars for one symbol, used for prior close / ATR."""

    @abstractmethod
    def get_news(self, symbol: str) -> list[dict]:
        """Recent headlines: [{headline, publisher, link, published_at}, ...]."""

    @abstractmethod
    def get_option_chain(self, symbol: str) -> list[dict] | None:
        """Flattened option contracts across the nearest expirations:
        [{expiration, option_type, strike, volume, open_interest, last_price, contract_symbol}, ...]
        Returns None if the symbol has no listed options.
        """
