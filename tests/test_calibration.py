import numpy as np
import pytest

from src.validation.calibration import apply_draw_calibration, calibration_slope_intercept


class TestCalibrationSlopeIntercept:
    def test_perfectly_calibrated_probabilities_give_slope_near_one_intercept_near_zero(self):
        rng = np.random.default_rng(0)
        n = 5000
        p_true = rng.uniform(0.05, 0.95, n)
        y = rng.binomial(1, p_true)

        a, b = calibration_slope_intercept(y, p_true)

        assert a == pytest.approx(0.0, abs=0.15)
        assert b == pytest.approx(1.0, abs=0.15)

    def test_overconfident_probabilities_give_slope_below_one(self):
        # Push every probability away from 0.5 (more extreme than the
        # true generating probability) -> classic overconfidence.
        rng = np.random.default_rng(1)
        n = 5000
        p_true = rng.uniform(0.2, 0.8, n)
        y = rng.binomial(1, p_true)
        p_overconfident = np.clip(0.5 + (p_true - 0.5) * 1.8, 0.01, 0.99)

        _, b = calibration_slope_intercept(y, p_overconfident)

        assert b < 1.0

    def test_too_few_observations_returns_nan(self):
        a, b = calibration_slope_intercept(np.array([1.0]), np.array([0.5]))
        assert np.isnan(a) and np.isnan(b)

    def test_no_outcome_variance_returns_nan(self):
        a, b = calibration_slope_intercept(np.zeros(20), np.full(20, 0.3))
        assert np.isnan(a) and np.isnan(b)


class TestApplyDrawCalibration:
    def test_identity_transform_is_a_no_op(self):
        h, d, a = apply_draw_calibration(0.35, 0.28, 0.37, intercept=0.0, slope=1.0)
        assert h == pytest.approx(0.35, abs=1e-9)
        assert d == pytest.approx(0.28, abs=1e-9)
        assert a == pytest.approx(0.37, abs=1e-9)

    def test_always_sums_to_one(self):
        h, d, a = apply_draw_calibration(0.30, 0.45, 0.25, intercept=-0.6, slope=0.4)
        assert h + d + a == pytest.approx(1.0, abs=1e-9)

    def test_overconfident_correction_pulls_draw_toward_fifty_fifty(self):
        # slope < 1 is the overconfidence case measured in the gate
        # report (model's draw calls are too extreme) -- correcting it
        # should move a draw probability away from the extremes and
        # toward 0.5, same direction calibration_slope_intercept itself
        # would estimate from overconfident predictions.
        h, d, a = apply_draw_calibration(0.30, 0.55, 0.15, intercept=0.0, slope=0.4)
        assert d < 0.55
        assert d > 0.5

    def test_preserves_home_away_ratio(self):
        h, d, a = apply_draw_calibration(0.35, 0.30, 0.35, intercept=-0.5, slope=0.6)
        assert h == pytest.approx(a, abs=1e-9)  # ratio preserved: both started equal

        h2, d2, a2 = apply_draw_calibration(0.50, 0.20, 0.30, intercept=-0.5, slope=0.6)
        assert h2 / a2 == pytest.approx(0.50 / 0.30, abs=1e-6)
