from app.scan import rvol


def test_compute_rvol_basic():
    assert rvol.compute_rvol(cum_volume_today=1000.0, cum_avg_volume=500.0) == 2.0


def test_compute_rvol_missing_average():
    assert rvol.compute_rvol(cum_volume_today=1000.0, cum_avg_volume=None) is None
    assert rvol.compute_rvol(cum_volume_today=1000.0, cum_avg_volume=0.0) is None


def test_compute_rvol_score_scales_to_cap():
    # default cap is 5.0 (config/settings.yaml) -> rvol == cap maps to 100
    assert rvol.compute_rvol_score(5.0) == 100.0
    assert rvol.compute_rvol_score(2.5) == 50.0
    assert rvol.compute_rvol_score(None) is None


def test_compute_rvol_score_clips_above_cap():
    assert rvol.compute_rvol_score(50.0) == 100.0
