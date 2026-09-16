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


class TestComputeGateDecision:
    def test_all_leagues_upper_bound_at_or_below_zero_repositions(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, -0.02, -0.05, -0.01)},
            "SP1": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(80, -0.01, -0.03, 0.0)},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_REPOSITION
        assert decision["leagues"] == []

    def test_some_league_lower_bound_above_zero_proceeds_with_that_league(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, 0.03, 0.01, 0.05)},
            "SP1": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(80, -0.01, -0.03, 0.0)},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_PROCEED_SUBSET
        assert decision["leagues"] == ["E0"]

    def test_straddling_zero_everywhere_is_inconclusive(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, 0.01, -0.02, 0.04)},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_INCONCLUSIVE

    def test_no_evaluable_league_is_inconclusive(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(5, float("nan"), float("nan"), float("nan"), insufficient=True)},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_INCONCLUSIVE
        assert "sufficient" in decision["reason"]

    def test_insufficient_sample_league_excluded_but_others_still_decide(self):
        clv_by_league = {
            "E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(5, float("nan"), float("nan"), float("nan"), insufficient=True)},
            "SP1": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, 0.03, 0.01, 0.05)},
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] == GATE_PROCEED_SUBSET
        assert decision["leagues"] == ["SP1"]


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
            "E0": {"baseline": _clv(500, 0.001, -0.01, 0.01), "flagged": _clv(100, 0.03, 0.01, 0.05)},
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
            },
        }
        decision = compute_gate_decision(clv_by_league)
        report = render_gate_report_markdown(clv_by_league, decision)
        assert "n/a" in report
        assert "0.00%" not in report

    def test_includes_exclusion_counts_when_given(self):
        clv_by_league = {"E0": {"baseline": _clv(500, 0.0, -0.01, 0.01), "flagged": _clv(100, 0.02, 0.0, 0.04)}}
        decision = compute_gate_decision(clv_by_league)
        exclusions = pd.DataFrame({"skip_reason": ["unseen_team"] * 3})
        report = render_gate_report_markdown(clv_by_league, decision, exclusions=exclusions)
        assert "unseen_team: 3" in report
