import numpy as np
import pytest

from src.validation.three_way import rps, three_way_log_loss


class TestThreeWayLogLoss:
    def test_perfect_confident_forecast_is_near_zero(self):
        y = np.array([0, 1, 2])
        p_home = np.array([0.999, 0.0005, 0.0005])
        p_draw = np.array([0.0005, 0.999, 0.0005])
        p_away = np.array([0.0005, 0.0005, 0.999])
        assert three_way_log_loss(y, p_home, p_draw, p_away) < 0.01

    def test_uniform_forecast_equals_log_of_three(self):
        y = np.array([0, 1, 2, 0])
        third = np.full(4, 1 / 3)
        loss = three_way_log_loss(y, third, third, third)
        assert loss == pytest.approx(np.log(3), abs=1e-6)

    def test_confidently_wrong_forecast_is_heavily_penalized(self):
        y = np.array([1])  # actual: Draw
        p_home = np.array([0.98])
        p_draw = np.array([0.01])
        p_away = np.array([0.01])
        assert three_way_log_loss(y, p_home, p_draw, p_away) > 4.0


class TestRps:
    def test_perfect_forecast_scores_zero(self):
        y = np.array([0])
        assert rps(y, np.array([1.0]), np.array([0.0]), np.array([0.0])) == pytest.approx(0.0, abs=1e-9)

    def test_uniform_forecast_matches_known_value(self):
        # Uniform (1/3,1/3,1/3) vs actual=Home: cum_p=[1/3,2/3,1], cum_e=[1,1,1]
        # -> ((2/3)^2 + (1/3)^2 + 0) / 2 = (4/9 + 1/9) / 2 = 5/18
        y = np.array([0])
        third = np.array([1 / 3])
        assert rps(y, third, third, third) == pytest.approx(5 / 18, abs=1e-9)

    def test_penalizes_far_miss_more_than_adjacent_miss(self):
        # Actual is Home. A forecast confidently predicting Draw
        # (adjacent on the ordinal scale) should score better than one
        # confidently predicting Away (the far end) -- this is exactly
        # the ordinal-distance sensitivity that distinguishes RPS from
        # log-loss/Brier, which would treat both misses identically.
        y = np.array([0])
        rps_draw_miss = rps(y, np.array([0.02]), np.array([0.96]), np.array([0.02]))
        rps_away_miss = rps(y, np.array([0.02]), np.array([0.02]), np.array([0.96]))
        assert rps_draw_miss < rps_away_miss

    def test_bounded_between_zero_and_one(self):
        rng = np.random.default_rng(0)
        raw = rng.dirichlet([1, 1, 1], size=50)
        y = rng.integers(0, 3, size=50)
        score = rps(y, raw[:, 0], raw[:, 1], raw[:, 2])
        assert 0.0 <= score <= 1.0
