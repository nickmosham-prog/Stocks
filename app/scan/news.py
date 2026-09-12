"""Fetches headlines for enriched tickers and tags them with simple keyword
matching (earnings, FDA, upgrade/downgrade, ...). Informational only - not
part of the numeric alpha score, so a bad tag never skews rankings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import db
from app.config import load_settings
from app.datasource.base import DataSource

RECENT_WINDOW = timedelta(hours=48)


def tag_headline(headline: str) -> list[str]:
    tags_cfg = load_settings().get("news_tags", default={}) or {}
    lowered = headline.lower()
    matched = []
    for tag, keywords in tags_cfg.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            matched.append(tag)
    return matched


def fetch_and_store_news(source: DataSource, symbol: str) -> bool:
    """Fetches + persists news for `symbol`. Returns has_recent_news (last 48h)."""
    items = source.get_news(symbol)
    now = datetime.now(tz=timezone.utc)
    has_recent = False

    for item in items:
        tags = tag_headline(item["headline"])
        published_at = item.get("published_at")
        is_recent = True
        if published_at:
            try:
                pub_dt = datetime.fromisoformat(published_at)
                is_recent = (now - pub_dt) <= RECENT_WINDOW
            except ValueError:
                is_recent = True
        has_recent = has_recent or is_recent

        with db.cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO news_items
                    (symbol, headline, publisher, link, published_at, tags, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    item["headline"],
                    item.get("publisher"),
                    item.get("link"),
                    published_at,
                    ",".join(tags),
                    now.isoformat(),
                ),
            )
    return has_recent
