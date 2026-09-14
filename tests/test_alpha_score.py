from app.scan import alpha_score


def test_alpha_score_all_components_present():
    # default weights: rvol 0.40, breakout 0.35, options 0.25 -> all scores 100 => 100
    score = alpha_score.compute_alpha_score(rvol_score=100.0, breakout_score=100.0, options_score=100.0)
    assert round(score, 2) == 100.0


def test_alpha_score_renormalizes_when_options_missing():
    # rvol=100, breakout=0, options missing -> renormalized weight is rvol/(rvol+breakout)
    score = alpha_score.compute_alpha_score(rvol_score=100.0, breakout_score=0.0, options_score=None)
    expected = 100.0 * (0.40 / (0.40 + 0.35))
    assert round(score, 4) == round(expected, 4)


def test_alpha_score_none_when_everything_missing():
    assert alpha_score.compute_alpha_score(None, None, None) is None


def test_preliminary_score_uses_available_components_only():
    assert alpha_score.preliminary_score(rvol_score=80.0, breakout_score=None) == 80.0
    assert alpha_score.preliminary_score(None, None) is None
    assert alpha_score.preliminary_score(60.0, 40.0) == 60.0 * 0.55 + 40.0 * 0.45


def test_nan_component_is_excluded_not_poisoning(monkeypatch):
    # Regression: a NaN component used to be treated as "present" (NaN is
    # not None) and poison the weighted sum into NaN instead of being
    # excluded like a real missing value.
    nan = float("nan")
    score = alpha_score.compute_alpha_score(rvol_score=nan, breakout_score=80.0, options_score=None)
    assert score == 80.0  # NaN rvol excluded entirely, same as if it were None


def test_all_nan_components_returns_none_not_nan():
    nan = float("nan")
    assert alpha_score.compute_alpha_score(nan, nan, nan) is None
