"""
Tests for R01: Chronological per-league xi decay parameter sweep.
"""
import numpy as np
import pandas as pd
import pytest

from src.validation.xi_sweep import sweep_all_leagues, sweep_league_xi


def _synthetic_chronological_matches(n_matches: int = 400, league: str = "E0") -> pd.DataFrame:
    rng = np.random.default_rng(42)
    start = pd.Timestamp("2022-08-01", tz="UTC")
    teams = ["ARS", "CHE", "LIV", "MCI", "MUN", "TOT", "NEW", "AVL", "WHU", "BHA"]

    rows = []
    for i in range(n_matches):
        home, away = rng.choice(teams, size=2, replace=False)
        hg, ag = int(rng.poisson(1.5)), int(rng.poisson(1.2))
        c_d = float(rng.uniform(3.1, 3.6))
        e_d = c_d * float(rng.normal(1.02, 0.03))
        rows.append({
            "date": start + pd.Timedelta(days=i * 2),
            "league": league,
            "season": "2223" if i < 200 else "2324",
            "home_team": home,
            "away_team": away,
            "home_goals": hg,
            "away_goals": ag,
            "result": "H" if hg > ag else ("A" if hg < ag else "D"),
            "entry_home": 2.2,
            "entry_draw": e_d,
            "entry_away": 3.4,
            "entry_source": "Pinnacle (opening)",
            "close_home": 2.2,
            "close_draw": c_d,
            "close_away": 3.4,
            "close_source": "Pinnacle closing",
            "retail_draw": c_d,
        })
    return pd.DataFrame(rows)


class TestXiSweep:
    def test_sweep_league_xi_chronological_split(self):
        matches = _synthetic_chronological_matches(n_matches=300, league="E0")
        res = sweep_league_xi(
            matches=matches,
            league="E0",
            xi_grid=[0.005, 0.008],
            split_ratio=0.70,
            min_train_matches=100,
            retrain_every_days=30,
        )
        assert res["status"] == "completed"
        assert res["tuning_matches"] == 210  # 70% of 300
        assert len(res["trials"]) == 2
        assert res["selected_xi"] in [0.005, 0.008]

    def test_sweep_league_insufficient_history(self):
        matches = _synthetic_chronological_matches(n_matches=50, league="E0")
        res = sweep_league_xi(
            matches=matches,
            league="E0",
            xi_grid=[0.005, 0.008],
            min_train_matches=100,
        )
        assert res["status"] == "insufficient_history"
        assert res["selected_xi"] is None

    def test_sweep_all_leagues_manifest(self, tmp_path):
        matches = _synthetic_chronological_matches(n_matches=300, league="E0")
        manifest_path = tmp_path / "xi_manifest.json"
        manifest = sweep_all_leagues(
            matches=matches,
            leagues=["E0"],
            xi_grid=[0.0065],
            split_ratio=0.70,
            output_manifest=manifest_path,
            min_train_matches=100,
        )
        assert manifest_path.exists()
        assert "E0" in manifest["leagues"]
        assert manifest["leagues"]["E0"]["selected_xi"] == 0.0065
