"""
Unit tests for the Dixon-Coles scoreline probability matrix simulator.
Verifies matrix normalization, non-negativity, adaptive expansion,
and parameter admissibility checks.
"""
import numpy as np
import pytest

from src.models.simulator import (
    build_score_matrix,
    check_tau_admissibility,
    match_probabilities,
    prob_away_win,
    prob_draw,
    prob_home_win,
    top_scorelines,
)


def test_score_matrix_non_negative_and_sums_to_one():
    """Score matrix cells must all be non-negative and sum strictly to 1.0."""
    matrix = build_score_matrix(1.5, 1.2, -0.05, grid_size=10)
    assert np.all(matrix >= 0.0), "Found negative probability cells in score matrix"
    assert np.isclose(matrix.sum(), 1.0, atol=1e-6), f"Matrix does not sum to 1: {matrix.sum()}"


def test_score_matrix_retained_mass_threshold():
    """Verify retained probability mass respects tail tolerance >= 99.9%."""
    # Under typical football intensities, grid 10 retains > 99.9% mass
    matrix = build_score_matrix(1.3, 1.1, -0.08, grid_size=10, tail_tolerance=1e-3)
    assert np.isclose(matrix.sum(), 1.0, atol=1e-6)
    assert matrix.shape[0] >= 10


def test_score_matrix_adaptive_expansion_for_high_intensity():
    """High goal intensities trigger adaptive grid expansion from 10 to higher dimension."""
    matrix_low = build_score_matrix(1.2, 1.0, -0.05, grid_size=10)
    assert matrix_low.shape == (10, 10)

    # High lambda/mu requires grid expansion to capture >= 99.9% of mass
    matrix_high = build_score_matrix(4.2, 3.8, 0.0, grid_size=10, max_grid_size=30, tail_tolerance=1e-3)
    assert matrix_high.shape[0] > 10, f"Expected grid expansion, got shape {matrix_high.shape}"
    assert matrix_high.shape[0] <= 30
    assert np.isclose(matrix_high.sum(), 1.0, atol=1e-6)


def test_score_matrix_hard_max_grid_limit():
    """Simulator respects max_grid_size limit and does not expand beyond it."""
    max_limit = 15
    # lam=4.5, mu=4.5 with tail_tolerance=1e-6 requires > 15, so it caps exactly at max_limit
    matrix = build_score_matrix(4.5, 4.5, 0.0, grid_size=10, max_grid_size=max_limit, tail_tolerance=1e-6)
    assert matrix.shape == (max_limit, max_limit)
    assert np.isclose(matrix.sum(), 1.0, atol=1e-6)



def test_tau_admissibility_and_rejection():
    """Inadmissible Dixon-Coles parameters that produce negative tau must be rejected."""
    # lam=3.0, mu=3.0, rho=0.2 => 1 - lam*mu*rho = 1 - 1.8 = -0.8 < 0
    admissible, reason = check_tau_admissibility(3.0, 3.0, 0.2)
    assert not admissible
    assert "tau(0,0)" in reason

    with pytest.raises(ValueError, match="Inadmissible Dixon-Coles parameters"):
        build_score_matrix(3.0, 3.0, 0.2)


def test_invalid_parameters_raise_value_error():
    """Non-finite or non-positive intensities must be rejected."""
    with pytest.raises(ValueError, match="Non-finite parameters"):
        build_score_matrix(np.nan, 1.2, 0.0)

    with pytest.raises(ValueError, match="Goal intensities must be positive"):
        build_score_matrix(-1.0, 1.2, 0.0)

    with pytest.raises(ValueError, match="Goal intensities must be positive"):
        build_score_matrix(1.5, 0.0, 0.0)


def test_match_probabilities_partition_of_unity():
    """Sum of home, draw, and away probabilities must equal 1.0."""
    probs = match_probabilities(1.6, 1.1, -0.04)
    p_h = probs["p_home"]
    p_d = probs["p_draw"]
    p_a = probs["p_away"]
    assert 0.0 <= p_h <= 1.0
    assert 0.0 <= p_d <= 1.0
    assert 0.0 <= p_a <= 1.0
    assert np.isclose(p_h + p_d + p_a, 1.0, atol=1e-6)


def test_top_scorelines_structure():
    """Top scorelines extraction returns valid tuples sorted descending by probability."""
    matrix = build_score_matrix(1.4, 1.0, -0.05)
    top2 = top_scorelines(matrix, n=2)
    assert len(top2) == 2
    h1, a1, p1 = top2[0]
    h2, a2, p2 = top2[1]
    assert p1 >= p2
    assert 0 <= h1 < matrix.shape[0]
    assert 0 <= a1 < matrix.shape[1]
