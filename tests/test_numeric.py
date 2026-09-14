import math

from app.scan.numeric import clean, is_valid


def test_is_valid_rejects_none_nan_inf():
    assert is_valid(None) is False
    assert is_valid(float("nan")) is False
    assert is_valid(float("inf")) is False
    assert is_valid(float("-inf")) is False


def test_is_valid_accepts_real_numbers():
    assert is_valid(0.0) is True
    assert is_valid(-12.5) is True
    assert is_valid(100) is True


def test_clean_converts_nan_and_inf_to_none():
    assert clean(float("nan")) is None
    assert clean(float("inf")) is None
    assert clean(None) is None


def test_clean_passes_through_real_numbers():
    assert clean(42.0) == 42.0
    assert clean(0.0) == 0.0
