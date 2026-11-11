"""Combines data sources: Yahoo for prices, fundamentals, news and the broad
options-flow scan; moomoo (when configured) for the option chains behind
specific option ideas, where accurate IV/Greeks matter most. Any moomoo
failure falls back to Yahoo for that call."""

from __future__ import annotations

import logging

import pandas as pd

from app.datasource.base import DataSource
from app.datasource.moomoo_source import MoomooOptionsClient
from app.datasource.yfinance_source import YFinanceSource

log = logging.getLogger(__name__)


class CompositeSource(DataSource):
    def __init__(self, base: DataSource, options_client: MoomooOptionsClient):
        self.base = base
        self.options_client = options_client
        self.last_options_provider = "moomoo"

    def _options(self, moomoo_call, yahoo_call):
        result = moomoo_call()
        if result:
            self.last_options_provider = "moomoo"
            return result
        self.last_options_provider = "yahoo (moomoo unavailable)"
        return yahoo_call()

    @property
    def options_provider_status(self) -> str:
        return self.last_options_provider

    def get_option_expirations(self, symbol: str) -> list[str] | None:
        return self._options(
            lambda: self.options_client.get_option_expirations(symbol),
            lambda: self.base.get_option_expirations(symbol),
        )

    def get_option_chain(self, symbol: str, expirations: list[str] | None = None) -> list[dict] | None:
        if expirations is None:
            # Broad options-flow scan (top-30 symbols every cycle): stays on
            # Yahoo to keep well inside moomoo's request limits.
            return self.base.get_option_chain(symbol)
        return self._options(
            lambda: self.options_client.get_option_chain(symbol, expirations),
            lambda: self.base.get_option_chain(symbol, expirations=expirations),
        )

    def get_option_marks(self, codes: list[str]) -> dict[str, float]:
        """Batched current prices for moomoo-coded option contracts (used by
        the scorecard). Empty when moomoo is unreachable."""
        return self.options_client.get_option_marks(codes) if codes else {}

    def get_bulk_intraday_bars(self, symbols, period, interval, prepost) -> dict[str, pd.DataFrame]:
        return self.base.get_bulk_intraday_bars(symbols, period, interval, prepost)

    def get_profile_history(self, symbol, days, interval):
        return self.base.get_profile_history(symbol, days, interval)

    def get_daily_history(self, symbol, days):
        return self.base.get_daily_history(symbol, days)

    def get_news(self, symbol):
        return self.base.get_news(symbol)

    def get_fundamentals(self, symbol):
        return self.base.get_fundamentals(symbol)

    def close(self) -> None:
        self.options_client.close()


def create_source(settings, base: DataSource | None = None) -> DataSource:
    base = base or YFinanceSource()
    provider = str(settings.get("options_source", "provider", default="yahoo")).lower()
    if provider != "moomoo":
        return base
    cfg = settings.get("options_source", "moomoo", default={}) or {}
    client = MoomooOptionsClient(host=cfg.get("host", "127.0.0.1"), port=int(cfg.get("port", 11111)))
    log.info("options data: moomoo OpenD at %s:%s (Yahoo as fallback)", cfg.get("host", "127.0.0.1"), cfg.get("port", 11111))
    return CompositeSource(base, client)


def options_provider_label(source: DataSource) -> str:
    return source.options_provider_status if isinstance(source, CompositeSource) else "yahoo"
