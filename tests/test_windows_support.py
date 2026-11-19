"""Running on Windows: keep-awake, UTF-8 config files and the launcher files."""

import sys
from pathlib import Path

import pytest

from app import config, keep_awake

ROOT = Path(__file__).resolve().parent.parent


class FakeKernel32:
    def __init__(self, result=1):
        self.calls = []
        self.result = result

    def SetThreadExecutionState(self, flags):
        self.calls.append(flags)
        return self.result


def test_keep_awake_does_nothing_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert keep_awake.prevent_sleep() is False
    keep_awake.release()  # no error


def test_keep_awake_asks_windows_to_stay_awake_and_releases(monkeypatch):
    kernel32 = FakeKernel32()
    monkeypatch.setattr(keep_awake, "_kernel32", lambda: kernel32)
    assert keep_awake.prevent_sleep() is True
    keep_awake.release()
    assert kernel32.calls == [0x80000001, 0x80000000]  # continuous + system required, then continuous only


def test_keep_awake_refused_or_broken_never_stops_the_screener(monkeypatch):
    monkeypatch.setattr(keep_awake, "_kernel32", lambda: FakeKernel32(result=0))
    assert keep_awake.prevent_sleep() is False

    class Broken:
        def SetThreadExecutionState(self, flags):
            raise OSError("no")

    monkeypatch.setattr(keep_awake, "_kernel32", lambda: Broken())
    assert keep_awake.prevent_sleep() is False
    keep_awake.release()


def test_config_files_are_read_as_utf8_whatever_the_system_encoding(tmp_path, monkeypatch):
    # Windows opens text files as cp1252 unless told otherwise, so every
    # config read must say utf-8 explicitly.
    real_open = open

    def strict_open(path, mode="r", *args, **kwargs):
        assert kwargs.get("encoding") == "utf-8", f"{path} opened without encoding='utf-8'"
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(config, "open", strict_open, raising=False)
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_bytes("app:\n  note: \"café — 東京\"\n".encode("utf-8"))
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_bytes("email:\n  from_name: \"Zoë\"\n".encode("utf-8"))
    monkeypatch.setattr(config, "SETTINGS_PATH", settings_file)
    monkeypatch.setattr(config, "SECRETS_PATH", secrets_file)
    config.load_settings.cache_clear()
    try:
        settings = config.load_settings()
        assert settings.get("app", "note") == "café — 東京"
        assert settings.get("email", "from_name") == "Zoë"
    finally:
        config.load_settings.cache_clear()


@pytest.mark.parametrize("name", [
    "Start Screener (Windows).bat",
    "Setup Screener (Windows).bat",
    "Update Screener (Windows).bat",
    "scripts/windows/open_opend.ps1",
    "scripts/windows/wait_and_open.ps1",
])
def test_windows_files_have_windows_line_endings(name):
    data = (ROOT / name).read_bytes()
    assert data.isascii()
    assert b"\n" in data and data.count(b"\r\n") == data.count(b"\n")


def test_start_file_points_at_files_that_exist():
    text = (ROOT / "Start Screener (Windows).bat").read_text()
    for helper in ("scripts\\windows\\open_opend.ps1", "scripts\\windows\\wait_and_open.ps1"):
        assert helper in text
        assert (ROOT / helper.replace("\\", "/")).exists()
    assert ".venv\\Scripts\\python.exe\" main.py" in text
