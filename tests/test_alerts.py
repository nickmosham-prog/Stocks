from app import alerts
from app.config import Settings


def _row(
    symbol="AAPL", alpha_score=90.0, price=150.0, rvol=4.2, gap_pct=6.5, session="regular",
    breakout_confirmed=0, breakout_direction=None, breakout_hold_minutes=None,
    fundamentals_status="pass",
):
    return {
        "symbol": symbol,
        "alpha_score": alpha_score,
        "price": price,
        "rvol": rvol,
        "gap_pct": gap_pct,
        "session": session,
        "breakout_confirmed": breakout_confirmed,
        "breakout_direction": breakout_direction,
        "breakout_hold_minutes": breakout_hold_minutes,
        "fundamentals_status": fundamentals_status,
        "buy_signal": 0,
        "breakdown_signal": 0,
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


def test_format_buy_setup_email_mentions_hold_time():
    row = _row(alpha_score=92.0, breakout_confirmed=1, breakout_direction="bullish", breakout_hold_minutes=22.4)
    subject, body = alerts._format_buy_setup_email([row])
    assert "BUY Setup" in subject
    assert "AAPL" in subject
    assert "held 22m" in body
    assert "trade instruction" in body.lower()


def _fake_settings_with_buy_setup(general_threshold=999, buy_threshold=88, **buy_overrides):
    buy_cfg = {
        "enabled": True,
        "alpha_score_threshold": buy_threshold,
        "require_bullish_direction": True,
        "require_confirmed_breakout": True,
        "require_fundamentals": True,
        "cooldown_minutes": 60,
        **buy_overrides,
    }
    return Settings(
        raw={
            "alerts": {
                "enabled": True,
                "alpha_score_threshold": general_threshold,  # disabled by default in these tests
                "cooldown_minutes": 60,
                "buy_setup": buy_cfg,
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


def _fake_settings_with_breakdown_setup(general_threshold=999, breakdown_threshold=88, **overrides):
    cfg = {
        "enabled": True,
        "alpha_score_threshold": breakdown_threshold,
        "require_bearish_direction": True,
        "require_confirmed_breakout": True,
        "cooldown_minutes": 60,
        **overrides,
    }
    return Settings(
        raw={
            "alerts": {
                "enabled": True,
                "alpha_score_threshold": general_threshold,
                "cooldown_minutes": 60,
                "breakdown_setup": cfg,
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


def _fake_settings_with_options_trade(**overrides):
    cfg = {"enabled": True, "cooldown_minutes": 240, **overrides}
    return Settings(
        raw={
            "alerts": {
                "enabled": True,
                "alpha_score_threshold": 999,  # disable other tiers for isolation
                "cooldown_minutes": 60,
                "options_trade": cfg,
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


def _capture_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_last_alert_time", lambda symbol, kind: None)
    monkeypatch.setattr(alerts, "_record_alert", lambda *a, **k: None)
    monkeypatch.setattr(alerts, "_send_email", lambda cfg, subject, body: sent.append((subject, body)) or True)
    return sent


def test_buy_setup_fires_when_confirmed_bullish_and_score_clears_bar(monkeypatch):
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_buy_setup())
    sent = _capture_sent(monkeypatch)

    row = _row(alpha_score=95.0, breakout_confirmed=1, breakout_direction="bullish", breakout_hold_minutes=20.0)
    alerts.process_alerts([row])

    assert len(sent) == 1
    assert "BUY Setup" in sent[0][0]


def test_buy_setup_does_not_fire_on_unconfirmed_breakout(monkeypatch):
    # High Alpha Score alone isn't enough - a fresh, unconfirmed trigger
    # must not fire the stricter BUY Setup tier.
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_buy_setup())
    sent = _capture_sent(monkeypatch)

    row = _row(alpha_score=95.0, breakout_confirmed=0, breakout_direction="bullish", breakout_hold_minutes=2.0)
    alerts.process_alerts([row])

    assert sent == []


def test_buy_setup_does_not_fire_on_bearish_breakout(monkeypatch):
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_buy_setup())
    sent = _capture_sent(monkeypatch)

    row = _row(alpha_score=95.0, breakout_confirmed=1, breakout_direction="bearish", breakout_hold_minutes=20.0)
    alerts.process_alerts([row])

    assert sent == []


def test_buy_setup_does_not_fire_below_its_own_threshold(monkeypatch):
    # Clears the general alert bar but not the stricter buy_setup bar.
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_buy_setup(buy_threshold=88))
    sent = _capture_sent(monkeypatch)

    row = _row(alpha_score=85.0, breakout_confirmed=1, breakout_direction="bullish", breakout_hold_minutes=20.0)
    alerts.process_alerts([row])

    assert sent == []


def test_general_and_buy_setup_tiers_are_independent(monkeypatch):
    # Both tiers enabled with low-enough thresholds that a single strong
    # row qualifies for both - should send two separate emails, one per
    # tier, each with its own subject.
    monkeypatch.setattr(
        alerts, "load_settings",
        lambda: _fake_settings_with_buy_setup(general_threshold=80, buy_threshold=88),
    )
    sent = _capture_sent(monkeypatch)

    row = _row(alpha_score=95.0, breakout_confirmed=1, breakout_direction="bullish", breakout_hold_minutes=20.0)
    alerts.process_alerts([row])

    assert len(sent) == 2
    subjects = [s for s, _ in sent]
    assert any(subj.startswith("Stock alert:") for subj in subjects)
    assert any(subj.startswith("BUY Setup:") for subj in subjects)


def test_buy_setup_does_not_fire_when_fundamentals_fail(monkeypatch):
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_buy_setup())
    sent = _capture_sent(monkeypatch)

    row = _row(
        alpha_score=95.0, breakout_confirmed=1, breakout_direction="bullish",
        breakout_hold_minutes=20.0, fundamentals_status="fail",
    )
    alerts.process_alerts([row])

    assert sent == []


def test_buy_setup_does_not_fire_when_fundamentals_unknown(monkeypatch):
    # Fail-closed: missing/unverifiable fundamentals never fire the email either.
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_buy_setup())
    sent = _capture_sent(monkeypatch)

    row = _row(
        alpha_score=95.0, breakout_confirmed=1, breakout_direction="bullish",
        breakout_hold_minutes=20.0, fundamentals_status="unknown",
    )
    alerts.process_alerts([row])

    assert sent == []


def test_breakdown_setup_fires_on_confirmed_bearish_breakdown(monkeypatch):
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_breakdown_setup())
    sent = _capture_sent(monkeypatch)

    row = _row(alpha_score=95.0, breakout_confirmed=1, breakout_direction="bearish", breakout_hold_minutes=20.0)
    alerts.process_alerts([row])

    assert len(sent) == 1
    assert "BREAKDOWN Setup" in sent[0][0]


def test_breakdown_setup_ignores_fundamentals(monkeypatch):
    # No fundamentals gate on the bearish tier at all.
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_breakdown_setup())
    sent = _capture_sent(monkeypatch)

    row = _row(
        alpha_score=95.0, breakout_confirmed=1, breakout_direction="bearish",
        breakout_hold_minutes=20.0, fundamentals_status="fail",
    )
    alerts.process_alerts([row])

    assert len(sent) == 1


def _trade_contract(option_type="call", strike=105.0, expiration="2026-10-15", days_to_expiration=35,
                     price=3.5, delta=0.65, theta=-0.04):
    return {
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
        "days_to_expiration": days_to_expiration,
        "price": price,
        "delta": delta,
        "theta": theta,
        "contract_symbol": "TESTC",
    }


def test_options_trade_fires_only_for_symbols_with_a_recommendation(monkeypatch):
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_options_trade())
    sent = _capture_sent(monkeypatch)

    row_with_rec = _row(symbol="AAPL", alpha_score=95.0)
    row_without_rec = _row(symbol="MSFT", alpha_score=96.0)
    trade_recs = {"AAPL": _trade_contract()}

    alerts.process_alerts([row_with_rec, row_without_rec], trade_recs)

    assert len(sent) == 1
    subject, body = sent[0]
    assert "AAPL" in subject
    assert "MSFT" not in subject
    assert "CALL" in body
    assert "trade\ninstruction" in body.lower()


def test_options_trade_does_not_fire_when_no_recommendations(monkeypatch):
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_options_trade())
    sent = _capture_sent(monkeypatch)

    alerts.process_alerts([_row(alpha_score=95.0)], {})

    assert sent == []


def test_options_trade_disabled_by_default_flag(monkeypatch):
    monkeypatch.setattr(alerts, "load_settings", lambda: _fake_settings_with_options_trade(enabled=False))
    sent = _capture_sent(monkeypatch)

    alerts.process_alerts([_row(alpha_score=95.0)], {"AAPL": _trade_contract()})

    assert sent == []
