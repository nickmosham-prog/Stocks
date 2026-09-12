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
