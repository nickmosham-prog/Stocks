"""Single entry point: `python main.py` starts the background scan scheduler
and the web dashboard together in one process.
"""

from __future__ import annotations

import atexit
import logging

import uvicorn

from app import db
from app.config import load_settings
from app.datasource.yfinance_source import YFinanceSource
from app.scheduler import bootstrap_if_needed, create_scheduler
from app.web.server import create_app


def _setup_logging(settings) -> None:
    log_path = settings.log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.get("app", "log_level", default="INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler()],
    )


def main() -> None:
    settings = load_settings()
    _setup_logging(settings)
    log = logging.getLogger("main")

    db.init_db()

    source = YFinanceSource()
    log.info("checking historical volume profile before starting scan loops...")
    bootstrap_if_needed(source)

    scheduler = create_scheduler(source)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))
    log.info("scheduler started (pre-market/intraday scans + daily EOD refresh)")

    app = create_app()
    host = settings.get("app", "host", default="127.0.0.1")
    port = settings.get("app", "port", default=8000)
    log.info("starting dashboard on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
