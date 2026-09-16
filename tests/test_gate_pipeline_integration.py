"""
End-to-end integration: run_league_evaluation -> evaluate_league_clv ->
compute_gate_decision -> render_gate_report_markdown, with more than 10
bets in play. This is exactly the composition that would have exposed
the original bootstrap_ci crash (a 2-D ROI-shaped resample) — none of
the per-module unit tests alone exercise the full chain.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.validation.clv import evaluate_league_clv
from src.validation.gate_harness import run_league_evaluation
from src.validation.gate_report import compute_gate_decision, render_gate_report_markdown
from src.validation.three_way import rps, three_way_log_loss


def _priced_matches(n_teams=10, rounds=8, seed=7, league="E0", season="2324") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    rows = []
    start = pd.Timestamp("2023-08-01", tz="UTC")
    day = 0
    for _ in range(rounds):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                hg, ag = int(rng.poisson(1.4)), int(rng.poisson(1.1))
                close_d = float(rng.uniform(3.0, 3.6))
                # Deliberate, modest positive entry-vs-close skew so
                # flagged bets have some real signal to detect, rather
                # than everything landing in "inconclusive" by chance.
                entry_d = close_d * float(rng.normal(1.01, 0.02))
                rows.append({
                    "date": start + pd.Timedelta(days=day), "league": league, "season": season,
                    "home_team": home, "away_team": away, "home_goals": hg, "away_goals": ag,
                    "result": "H" if hg > ag else ("A" if hg < ag else "D"),
                    "entry_home": 2.0, "entry_draw": entry_d, "entry_away": 3.8,
                    "entry_source": "Pinnacle (opening)",
                    "close_home": 2.0, "close_draw": close_d, "close_away": 3.8,
                    "close_source": "Pinnacle closing",
                    "retail_draw": close_d,
                })
                day += 1
    return pd.DataFrame(rows)


class TestGatePipelineIntegration:
    def test_full_chain_produces_a_decision_and_report_with_more_than_ten_bets(self):
        matches = _priced_matches(n_teams=10, rounds=8)  # 720 matches -> well over 10 qualified bets expected

        results, exclusions = run_league_evaluation(
            matches, "E0", xi=0.0065, min_train_matches=100, retrain_every_days=14,
        )
        assert len(results) > 10

        clv = evaluate_league_clv(
            results, entry_col="entry_draw", close_col="close_draw",
            flagged_mask=results["qualified"], block_col="matchweek",
        )
        # This exact call shape (a DataFrame slice with >10 rows feeding
        # the ROI/CLV bootstrap) is what crashed the original
        # np.random.choice(data, ...)-based bootstrap_ci on 2-D input.
        assert clv["baseline"]["n"] > 10

        clv_by_league = {"E0": clv}
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] in {"reposition_as_forecasting_tool", "proceed_with_leagues", "inconclusive"}

        three_way_by_league = {
            "E0": {
                "log_loss": three_way_log_loss(
                    results["actual_outcome_idx"], results["p_home"], results["p_draw"], results["p_away"],
                ),
                "rps": rps(
                    results["actual_outcome_idx"], results["p_home"], results["p_draw"], results["p_away"],
                ),
            },
        }

        report = render_gate_report_markdown(clv_by_league, decision, exclusions=exclusions, three_way_by_league=three_way_by_league)

        assert "WP3 Gate Report" in report
        assert "E0" in report
        assert decision["decision"] in report
