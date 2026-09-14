from app import alerts
from app.config import Settings


def _row(symbol="AAPL", alpha_score=90.0, price=150.0, rvol=4.2, gap_pct=6.5, session="regular"):
    return {
        "symbol": symbol,
        "alpha_score": alpha_score,
        "price": price,
        "rvol": rvol,
        "gap_pct": gap_pct,
        "session": session,
    }


def test_format_email_single_ticker_subject():
    subject, body = alerts._format_email([_row()])
    assert "AAPL" in subject
    assert "90" in subject
    assert "AAPL" in body


def test_format_email_multiple_tickers_subject():
    subject, body = alerts._format_email([_row("AAPL"), _row("TSLA", alpha_score=85.0)])
    assert "2 tickers" in subject
    assert "AAPL" in subject and "TSLA" in subject
    assert "AAPL" in body and "TSLA" in body


def test_is_configured_rejects_default_placeholder():
    cfg = {"app_password": "CHANGE_ME", "from_address": "you@gmail.com"}
    assert alerts._is_configured(cfg) is False


def test_is_configured_accepts_real_looking_credentials():
    cfg = {"app_password": "abcd efgh ijkl mnop", "from_address": "trader@gmail.com"}
    assert alerts._is_configured(cfg) is True


def test_process_alerts_noop_when_unconfigured(monkeypatch):
    # Default config/settings.yaml ships with app_password: CHANGE_ME, so this
    # should short-circuit before ever touching the database or sending mail.
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("should not attempt to send when unconfigured")

    monkeypatch.setattr(alerts, "_send_email", _fail_if_called)
    alerts.process_alerts([_row(alpha_score=99.0)])  # should return quietly, no exception


def _fake_configured_settings():
    return Settings(
        raw={
            "alerts": {
                "enabled": True,
                "alpha_score_threshold": 80,
                "cooldown_minutes": 60,
                "email": {
                    "smtp_host": "smtp.gmail.com",
                    "smtp_port": 587,
                    "from_address": "trader@gmail.com",
                    "app_password": "abcd efgh ijkl mnop",
                    "to_address": "trader@gmail.com",
                },
            }
        }
    )


def test_process_alerts_never_fires_on_nan_alpha_score(monkeypatch):
    # Regression: `nan is None` is False and `nan < threshold` is also
    # False, so the old `if alpha is None or alpha < threshold: continue`
    # guard let a NaN score sail straight through as "qualifying".
    monkeypatch.setattr(alerts, "load_settings", _fake_configured_settings)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("must never send an alert for a NaN alpha score")

    monkeypatch.setattr(alerts, "_send_email", _fail_if_called)
    alerts.process_alerts([_row(alpha_score=float("nan"))])
