import threading

import pytest

from app import db
from app.config import load_settings


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """A fresh, fully-migrated SQLite database in a temp folder."""
    settings = load_settings()
    monkeypatch.setitem(settings.raw["app"], "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(db, "_local", threading.local())
    db.init_db()
    yield db
    db.get_connection().close()
