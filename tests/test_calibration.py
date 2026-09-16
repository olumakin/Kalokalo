import numpy as np
import pytest

from src.validation.calibration import calibration_slope_intercept


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
