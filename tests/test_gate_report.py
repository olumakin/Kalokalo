import pandas as pd
import pytest

from src.validation.gate_report import (
    GATE_INCONCLUSIVE,
    GATE_PROCEED_SUBSET,
    GATE_REPOSITION,
    compute_gate_decision,
    render_gate_report_markdown,
    summarize_exclusions,
)


def _clv(n, mean_clv, ci_lo, ci_hi, insufficient=False):
    return {"n": n, "mean_clv": mean_clv, "ci_lo": ci_lo, "ci_hi": ci_hi, "insufficient_sample": insufficient}


def _diff(mean_diff, ci_lo, ci_hi, insufficient=False):
    return {"mean_diff": mean_diff, "ci_lo": ci_lo, "ci_hi": ci_hi, "insufficient_sample": insufficient}


# Default diff: clears 0 (positive, interval entirely above 0) -- most
# tests below are about the flagged-CLV criterion itself and shouldn't
# be tripped up by the (separate) diff criterion unless a test says so.
_CLEARING_DIFF = _diff(0.02, 0.01, 0.03)


class TestComputeGateDecision:
    def test_all_leagues_upper_bound_at_or_below_zero_repositions(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, -0.02, -0.05, -0.01), "diff": _diff(-0.02, -0.05, -0.01)},
            "SP1": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(80, -0.01, -0.03, 0.0), "diff": _diff(-0.01, -0.03, 0.0)},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_REPOSITION
        assert decision["leagues"] == []

    def test_some_league_lower_bound_above_zero_proceeds_with_that_league(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, 0.03, 0.01, 0.05), "diff": _CLEARING_DIFF},
            "SP1": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(80, -0.01, -0.03, 0.0), "diff": _diff(-0.01, -0.03, 0.0)},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_PROCEED_SUBSET
        assert decision["leagues"] == ["E0"]

    def test_straddling_zero_everywhere_is_inconclusive(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, 0.01, -0.02, 0.04), "diff": _CLEARING_DIFF},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_INCONCLUSIVE

    def test_no_evaluable_league_is_inconclusive(self):
        clv_by_league = {
            "E0": {
                "baseline": _clv(500, 0.0, -0.01, 0.01),
                "flagged": _clv(5, float("nan"), float("nan"), float("nan"), insufficient=True),
                "diff": _diff(float("nan"), float("nan"), float("nan"), insufficient=True),
            },
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_INCONCLUSIVE
        assert "sufficient" in decision["reason"]

    def test_insufficient_sample_league_excluded_but_others_still_decide(self):
        clv_by_league = {
            "E0": {
                "baseline": _clv(500, 0.0, -0.01, 0.01),
                "flagged": _clv(5, float("nan"), float("nan"), float("nan"), insufficient=True),
                "diff": _diff(float("nan"), float("nan"), float("nan"), insufficient=True),
            },
            "SP1": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, 0.03, 0.01, 0.05), "diff": _CLEARING_DIFF},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_PROCEED_SUBSET
        assert decision["leagues"] == ["SP1"]

    def test_positive_flagged_clv_without_a_clearing_diff_does_not_proceed(self):
        # The exact pattern the diff criterion was added for: flagged
        # CLV clears 0 on its own, but the model's selection isn't
        # distinguishable from naively betting every draw (diff CI
        # straddles 0) -- this league must NOT be in proceed_leagues
        # even though the old flagged-CLV-only rule would have passed it.
        clv_by_league = {
            "D1": {"baseline": _clv(1677, 0.0085, 0.001, 0.016), "flagged": _clv(594, 0.0076, 0.0008, 0.0144), "diff": _diff(-0.0009, -0.0052, 0.0037)},
            "I1": {"baseline": _clv(2134, 0.0123, 0.005, 0.019), "flagged": _clv(832, 0.0189, 0.0144, 0.0232), "diff": _diff(0.0065, 0.0034, 0.0094)},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_PROCEED_SUBSET
        assert decision["leagues"] == ["I1"]

    def test_league_with_insufficient_diff_sample_but_sufficient_flagged_sample_is_excluded(self):
        clv_by_league = {
            "E0": {
                "baseline": _clv(500, 0.0, -0.01, 0.01),
                "flagged": _clv(100, 0.03, 0.01, 0.05),
                "diff": _diff(float("nan"), float("nan"), float("nan"), insufficient=True),
            },
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_INCONCLUSIVE
        assert "sufficient" in decision["reason"]


class TestSummarizeExclusions:
    def test_counts_by_reason(self):
        exclusions = pd.DataFrame({"skip_reason": ["unseen_team", "unseen_team", "insufficient_league_history"]})
        counts = summarize_exclusions(exclusions)
        assert counts == {"unseen_team": 2, "insufficient_league_history": 1}

    def test_empty_returns_empty_dict(self):
        assert summarize_exclusions(pd.DataFrame(columns=["skip_reason"])) == {}


class TestRenderGateReportMarkdown:
    def test_produces_nonempty_markdown_with_decision_and_leagues(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.001, -0.01, 0.01), "flagged": _clv(100, 0.03, 0.01, 0.05), "diff": _CLEARING_DIFF},
        }
        decision = compute_gate_decision(clv_by_league)
        report = render_gate_report_markdown(clv_by_league, decision)
        assert GATE_PROCEED_SUBSET in report
        assert "E0" in report
        assert "0.001" not in report  # formatted as percent, not raw

    def test_insufficient_sample_shows_na_not_zero(self):
        clv_by_league = {
            "E0": {
                "baseline": _clv(500, 0.001, -0.01, 0.01),
                "flagged": _clv(5, float("nan"), float("nan"), float("nan"), insufficient=True),
                "diff": _diff(float("nan"), float("nan"), float("nan"), insufficient=True),
            },
        }
        decision = compute_gate_decision(clv_by_league)
        report = render_gate_report_markdown(clv_by_league, decision)
        assert "n/a" in report
        assert "0.00%" not in report

    def test_includes_exclusion_counts_when_given(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, 0.02, 0.0, 0.04), "diff": _CLEARING_DIFF},
        }
        decision = compute_gate_decision(clv_by_league)
        exclusions = pd.DataFrame({"skip_reason": ["unseen_team"] * 3})
        report = render_gate_report_markdown(clv_by_league, decision, exclusions=exclusions)
        assert "unseen_team: 3" in report

    def test_includes_diff_column(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, 0.03, 0.01, 0.05), "diff": _diff(0.02, 0.005, 0.035)},
        }
        decision = compute_gate_decision(clv_by_league)
        report = render_gate_report_markdown(clv_by_league, decision)
        assert "Diff vs. baseline" in report
        assert "+2.00%" in report
