import pytest

from src.analytics.edge import apply_risk_caps, calculate_ev, kelly_fraction, qualifies


class TestEVAndQualification:
    def test_calculate_ev_positive(self):
        ev = calculate_ev(model_p_draw=0.32, market_odds_draw=3.40)
        assert ev == pytest.approx(0.32 * 3.40 - 1)
        assert ev > 0

    def test_qualifies_requires_min_ev_and_model_edge(self):
        # EV above threshold but model does not actually favor draw more than market
        assert qualifies(model_p_draw=0.28, market_p_draw=0.30, ev=0.05, min_ev=0.03) is False
        # EV below threshold, model favors draw
        assert qualifies(model_p_draw=0.32, market_p_draw=0.29, ev=0.02, min_ev=0.03) is False
        # Both satisfied
        assert qualifies(model_p_draw=0.32, market_p_draw=0.29, ev=0.05, min_ev=0.03) is True


class TestKellyFraction:
    def test_kelly_fraction_positive_for_positive_edge(self):
        f = kelly_fraction(p=0.32, odds=3.40, c=0.15)
        assert f > 0

    def test_kelly_fraction_zero_for_negative_edge(self):
        f = kelly_fraction(p=0.20, odds=3.40, c=0.15)
        assert f == 0.0


class TestSingleMatchCap:
    def test_stake_clipped_to_single_match_cap(self):
        # Deliberately huge raw Kelly stake for one match
        stakes = {"match_1": 0.20}
        capped = apply_risk_caps(stakes, single_match_cap=0.025, daily_slate_cap=0.08)
        assert capped["match_1"] == pytest.approx(0.025)

    def test_stake_under_cap_is_unchanged(self):
        stakes = {"match_1": 0.01}
        capped = apply_risk_caps(stakes, single_match_cap=0.025, daily_slate_cap=0.08)
        assert capped["match_1"] == pytest.approx(0.01)


class TestDailySlateCap:
    def test_total_exposure_scaled_down_when_over_cap(self):
        # Five matches each at the single-match cap (2.5%) -> 12.5% total,
        # which exceeds the 8% daily slate cap and must be scaled down.
        stakes = {f"match_{i}": 0.025 for i in range(5)}
        capped = apply_risk_caps(stakes, single_match_cap=0.025, daily_slate_cap=0.08)
        assert sum(capped.values()) == pytest.approx(0.08, abs=1e-9)
        # Proportional scaling preserves relative weights (all equal here)
        assert len(set(round(v, 10) for v in capped.values())) == 1

    def test_total_exposure_unchanged_when_under_cap(self):
        stakes = {"match_1": 0.01, "match_2": 0.015}
        capped = apply_risk_caps(stakes, single_match_cap=0.025, daily_slate_cap=0.08)
        assert capped == pytest.approx(stakes)

    def test_scaled_stakes_still_respect_relative_proportions(self):
        stakes = {"match_1": 0.02, "match_2": 0.01}
        # Force scaling by lowering the daily cap below the raw total (0.03)
        capped = apply_risk_caps(stakes, single_match_cap=0.025, daily_slate_cap=0.015)
        assert sum(capped.values()) == pytest.approx(0.015, abs=1e-9)
        assert capped["match_1"] / capped["match_2"] == pytest.approx(2.0)
