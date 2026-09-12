"""Loads config/settings.yaml and config/watchlist.yaml into plain objects."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"
WATCHLIST_PATH = REPO_ROOT / "config" / "watchlist.yaml"


@dataclass(frozen=True)
class Settings:
    raw: dict

    def __getitem__(self, key):
        return self.raw[key]

    def get(self, *keys, default=None):
        """Nested lookup: settings.get('scoring', 'rvol', 'cap')."""
        node = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def db_path(self) -> Path:
        return REPO_ROOT / self.get("app", "db_path", default="data/stocks.db")

    @property
    def log_path(self) -> Path:
        return REPO_ROOT / self.get("app", "log_path", default="logs/stocks.log")


@functools.lru_cache(maxsize=1)
def load_settings() -> Settings:
    with open(SETTINGS_PATH, "r") as f:
        raw = yaml.safe_load(f)
    return Settings(raw=raw)


@functools.lru_cache(maxsize=1)
def load_watchlist() -> list[str]:
    with open(WATCHLIST_PATH, "r") as f:
        raw = yaml.safe_load(f)
    tickers = raw.get("tickers", [])
    # de-dupe, preserve order, normalize case
    seen = set()
    out = []
    for t in tickers:
        symbol = str(t).strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out
