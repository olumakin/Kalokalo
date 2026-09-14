import pytest

from src.analytics.devig import multiplicative_devig, overround, shin_devig


ODDS_SETS = [
    (2.10, 3.40, 3.60),   # typical Big 5 draw market, ~5% overround
    (1.50, 4.20, 6.50),   # strong favorite
    (2.70, 3.30, 2.80),   # tight three-way market
]


class TestMultiplicativeDevig:
    @pytest.mark.parametrize("odds_h,odds_d,odds_a", ODDS_SETS)
    def test_sums_to_one(self, odds_h, odds_d, odds_a):
        p_h, p_d, p_a = multiplicative_devig(odds_h, odds_d, odds_a)
        assert p_h + p_d + p_a == pytest.approx(1.0, abs=1e-9)

    @pytest.mark.parametrize("odds_h,odds_d,odds_a", ODDS_SETS)
    def test_all_probabilities_positive(self, odds_h, odds_d, odds_a):
        p_h, p_d, p_a = multiplicative_devig(odds_h, odds_d, odds_a)
        assert p_h > 0 and p_d > 0 and p_a > 0

    def test_overround_exceeds_one_for_real_book(self):
        assert overround(2.10, 3.40, 3.60) > 1.0

    def test_devig_reduces_probability_relative_to_raw(self):
        odds_h, odds_d, odds_a = 2.10, 3.40, 3.60
        p_h, p_d, p_a = multiplicative_devig(odds_h, odds_d, odds_a)
        raw_h = 1 / odds_h
        assert p_h < raw_h  # margin removed strictly reduces the raw implied probability


class TestShinDevig:
    @pytest.mark.parametrize("odds_h,odds_d,odds_a", ODDS_SETS)
    def test_sums_to_one(self, odds_h, odds_d, odds_a):
        p_h, p_d, p_a = shin_devig(odds_h, odds_d, odds_a)
        assert p_h + p_d + p_a == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.parametrize("odds_h,odds_d,odds_a", ODDS_SETS)
    def test_all_probabilities_positive(self, odds_h, odds_d, odds_a):
        p_h, p_d, p_a = shin_devig(odds_h, odds_d, odds_a)
        assert p_h > 0 and p_d > 0 and p_a > 0

    def test_zero_margin_book_normalizes_without_error(self):
        # Fair (zero-margin) book: implied probabilities already sum to 1
        p_h, p_d, p_a = shin_devig(3.0, 3.0, 3.0)
        assert p_h + p_d + p_a == pytest.approx(1.0, abs=1e-6)
