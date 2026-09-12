from app.config import _deep_merge


def test_deep_merge_overrides_nested_keys_without_dropping_siblings():
    base = {
        "alerts": {
            "enabled": True,
            "email": {"from_address": "a@example.com", "app_password": "CHANGE_ME", "to_address": "a@example.com"},
        }
    }
    overrides = {"alerts": {"email": {"app_password": "real-secret"}}}

    merged = _deep_merge(base, overrides)

    assert merged["alerts"]["email"]["app_password"] == "real-secret"
    # sibling keys untouched by the override are preserved
    assert merged["alerts"]["email"]["from_address"] == "a@example.com"
    assert merged["alerts"]["enabled"] is True


def test_deep_merge_does_not_mutate_inputs():
    base = {"a": {"b": 1}}
    overrides = {"a": {"c": 2}}
    merged = _deep_merge(base, overrides)
    assert base == {"a": {"b": 1}}  # base untouched
    assert merged == {"a": {"b": 1, "c": 2}}
