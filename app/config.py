"""Loads config/settings.yaml and config/watchlist.yaml into plain objects."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"
WATCHLIST_PATH = REPO_ROOT / "config" / "watchlist.yaml"
# Git-ignored, machine-local overrides (real credentials go here, never in
# settings.yaml, so they can't end up committed to the repo). See
# config/secrets.yaml.example for the expected shape.
SECRETS_PATH = REPO_ROOT / "config" / "secrets.yaml"


def _deep_merge(base: dict, overrides: dict) -> dict:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


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
    if SECRETS_PATH.exists():
        with open(SECRETS_PATH, "r") as f:
            secrets = yaml.safe_load(f) or {}
        raw = _deep_merge(raw, secrets)
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
