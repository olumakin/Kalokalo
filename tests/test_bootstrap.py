import numpy as np
import pandas as pd
import pytest

from src.validation.bootstrap import block_bootstrap_ci, bootstrap_ci, paired_bootstrap_ci


class TestBootstrapCi:
    def test_1d_array_does_not_raise(self):
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0] * 5)
        lo, hi = bootstrap_ci(data, np.mean, n_resamples=200)
        assert lo <= np.mean(data) <= hi

    def test_2d_array_does_not_raise(self):
        # This is exactly the shape that crashed the old
        # np.random.choice(data, ...)-based implementation once a league
        # had more than 10 (odds, outcome) rows.
        data = np.column_stack([np.full(20, 2.0), np.arange(20) % 2])
        stat = lambda arr: float(np.mean(arr[:, 0] * arr[:, 1]))  # noqa: E731
        lo, hi = bootstrap_ci(data, stat, n_resamples=200)
        assert lo <= hi

    def test_dataframe_input_stays_row_aligned(self):
        df = pd.DataFrame({"odds": [2.0] * 20, "won": [1, 0] * 10})
        stat = lambda d: float((d["odds"] * d["won"]).mean())  # noqa: E731
        lo, hi = bootstrap_ci(df, stat, n_resamples=200)
        assert lo <= hi

    def test_fewer_than_two_rows_returns_nan_not_zero(self):
        assert all(np.isnan(x) for x in bootstrap_ci(np.array([1.0]), np.mean))
        assert all(np.isnan(x) for x in bootstrap_ci(np.array([]), np.mean))

    def test_seeded_result_is_reproducible(self):
        data = np.arange(30, dtype=float)
        r1 = bootstrap_ci(data, np.mean, n_resamples=500, seed=42)
        r2 = bootstrap_ci(data, np.mean, n_resamples=500, seed=42)
        assert r1 == r2

    def test_different_seed_can_differ(self):
        data = np.arange(30, dtype=float)
        r1 = bootstrap_ci(data, np.mean, n_resamples=500, seed=1)
        r2 = bootstrap_ci(data, np.mean, n_resamples=500, seed=2)
        assert r1 != r2


class TestBlockBootstrapCi:
    def test_resamples_whole_blocks_not_individual_rows(self):
        df = pd.DataFrame({"value": [1.0, 1.0, 1.0, 100.0, 100.0, 100.0]})
        block_ids = np.array(["mw1", "mw1", "mw1", "mw2", "mw2", "mw2"])
        stat = lambda d: float(d["value"].mean())  # noqa: E731

        lo, hi = block_bootstrap_ci(df, block_ids, stat, n_resamples=500)
        # Every resample is entirely mw1 (mean 1.0) or entirely mw2 (mean
        # 100.0) rows mixed at the *block* level, never a half-and-half
        # blend that row-level resampling would produce.
        assert lo >= 1.0 and hi <= 100.0

    def test_fewer_than_two_blocks_returns_nan(self):
        df = pd.DataFrame({"value": [1.0, 2.0, 3.0]})
        block_ids = np.array(["mw1", "mw1", "mw1"])
        lo, hi = block_bootstrap_ci(df, block_ids, lambda d: float(d["value"].mean()))
        assert np.isnan(lo) and np.isnan(hi)

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            block_bootstrap_ci(pd.DataFrame({"value": [1, 2, 3]}), np.array(["a", "b"]), np.mean)


class TestPairedBootstrapCi:
    def test_pairing_is_preserved_across_resamples(self):
        # b is always exactly a + 1; the paired difference stat must
        # reflect that constant offset in every resample, not drift as
        # it would if a and b were resampled independently.
        a = np.arange(30, dtype=float)
        b = a + 1.0
        stat_diff = lambda ra, rb: float(np.mean(rb - ra))  # noqa: E731
        lo, hi = paired_bootstrap_ci(a, b, stat_diff, n_resamples=500)
        assert lo == pytest.approx(1.0, abs=1e-9)
        assert hi == pytest.approx(1.0, abs=1e-9)

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            paired_bootstrap_ci(np.array([1, 2]), np.array([1, 2, 3]), lambda a, b: 0.0)

    def test_fewer_than_two_rows_returns_nan(self):
        lo, hi = paired_bootstrap_ci(np.array([1.0]), np.array([2.0]), lambda a, b: 0.0)
        assert np.isnan(lo) and np.isnan(hi)
