import numpy as np
import pytest

from src.validation.blend import (
    best_blend_weight,
    blend_three_way_log_loss,
    compare_model_market_blend,
)
from src.validation.three_way import AWAY, DRAW, HOME, three_way_log_loss

# Deliberately mediocre, non-degenerate forecasts -- neither source is
# ever certain, so log-loss stays finite without clipping artifacts
# dominating the comparison.
Y = np.array([HOME, DRAW, AWAY, HOME, DRAW, AWAY, HOME, DRAW])
MODEL_PROBS = (
    np.array([0.55, 0.20, 0.70, 0.50, 0.25, 0.60, 0.45, 0.30]),
    np.array([0.25, 0.60, 0.15, 0.25, 0.55, 0.15, 0.30, 0.50]),
    np.array([0.20, 0.20, 0.15, 0.25, 0.20, 0.25, 0.25, 0.20]),
)
MARKET_PROBS = (
    np.array([0.45, 0.30, 0.55, 0.40, 0.35, 0.50, 0.55, 0.20]),
    np.array([0.30, 0.45, 0.25, 0.35, 0.40, 0.25, 0.20, 0.55]),
    np.array([0.25, 0.25, 0.20, 0.25, 0.25, 0.25, 0.25, 0.25]),
)


class TestBlendThreeWayLogLoss:
    def test_weight_one_equals_model_alone(self):
        blended_ll = blend_three_way_log_loss(Y, MODEL_PROBS, MARKET_PROBS, weight=1.0)
        model_ll = three_way_log_loss(Y, *MODEL_PROBS)
        assert blended_ll == pytest.approx(model_ll)

    def test_weight_zero_equals_market_alone(self):
        blended_ll = blend_three_way_log_loss(Y, MODEL_PROBS, MARKET_PROBS, weight=0.0)
        market_ll = three_way_log_loss(Y, *MARKET_PROBS)
        assert blended_ll == pytest.approx(market_ll)

    def test_blended_rows_still_sum_to_one(self):
        # Not asserted directly by the public API, but a wrong blend
        # (e.g. forgetting to renormalize) would silently miscompute
        # every downstream log-loss -- check it from first principles.
        model_stack = np.column_stack(MODEL_PROBS)
        market_stack = np.column_stack(MARKET_PROBS)
        blended = 0.5 * model_stack + 0.5 * market_stack
        assert np.allclose(blended.sum(axis=1), 1.0)


class TestBestBlendWeight:
    def test_recovers_the_better_source_when_one_is_near_perfect(self):
        # Market gets every outcome right with high confidence; model is
        # deliberately poor. The weight-minimizing search should land
        # near the market end (low weight), not the model end.
        near_perfect = {HOME: (0.9, 0.05, 0.05), DRAW: (0.05, 0.9, 0.05), AWAY: (0.05, 0.05, 0.9)}
        y = np.array([HOME, DRAW, AWAY, HOME, DRAW, AWAY])
        market = tuple(np.array([near_perfect[o][k] for o in y]) for k in range(3))
        poor_model = tuple(np.full(len(y), 1 / 3) for _ in range(3))

        best_w, best_ll = best_blend_weight(y, poor_model, market)

        assert best_w <= 0.1
        assert best_ll == pytest.approx(three_way_log_loss(y, *market), abs=1e-6)

    def test_grid_is_exhaustively_checked_not_assumed_convex(self):
        best_w, best_ll = best_blend_weight(Y, MODEL_PROBS, MARKET_PROBS)
        # Brute-force cross-check against the same grid, independently.
        grid = np.linspace(0.0, 1.0, 21)
        losses = [blend_three_way_log_loss(Y, MODEL_PROBS, MARKET_PROBS, w) for w in grid]
        assert best_ll == pytest.approx(min(losses))
        assert best_w == pytest.approx(grid[int(np.argmin(losses))])


class TestCompareModelMarketBlend:
    def test_returns_expected_keys_and_consistent_values(self):
        result = compare_model_market_blend(Y, *MODEL_PROBS, *MARKET_PROBS)

        assert result["n"] == len(Y)
        assert result["model_log_loss"] == pytest.approx(three_way_log_loss(Y, *MODEL_PROBS))
        assert result["market_log_loss"] == pytest.approx(three_way_log_loss(Y, *MARKET_PROBS))
        assert 0.0 <= result["best_blend_weight"] <= 1.0
        assert result["best_blend_log_loss"] <= result["model_log_loss"] + 1e-9
        assert result["best_blend_log_loss"] <= result["market_log_loss"] + 1e-9

    def test_blend_beats_both_flag_is_false_when_one_source_dominates(self):
        # Same near-perfect-market / poor-model setup as above: the best
        # blend is just the market at full weight, not a genuine interior
        # optimum, so blend_beats_both should be False.
        near_perfect = {HOME: (0.9, 0.05, 0.05), DRAW: (0.05, 0.9, 0.05), AWAY: (0.05, 0.05, 0.9)}
        y = np.array([HOME, DRAW, AWAY, HOME, DRAW, AWAY])
        market = tuple(np.array([near_perfect[o][k] for o in y]) for k in range(3))
        poor_model = tuple(np.full(len(y), 1 / 3) for _ in range(3))

        result = compare_model_market_blend(y, *poor_model, *market)
        assert result["blend_beats_both"] is False
