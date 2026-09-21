"""
Tests for R02: Full evidence pack orchestrator and report compilation.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.validation.gate_pipeline import run_evidence_pack


def _synthetic_matches(n_matches: int = 250, league: str = "E0") -> pd.DataFrame:
    rng = np.random.default_rng(123)
    start = pd.Timestamp("2023-01-01", tz="UTC")
    teams = ["ARS", "CHE", "LIV", "MCI", "MUN", "TOT", "NEW", "AVL"]

    rows = []
    for i in range(n_matches):
        home, away = rng.choice(teams, size=2, replace=False)
        hg, ag = int(rng.poisson(1.3)), int(rng.poisson(1.1))
        c_d = float(rng.uniform(3.1, 3.5))
        e_d = c_d * float(rng.normal(1.02, 0.02))
        rows.append({
            "date": start + pd.Timedelta(days=i * 2),
            "league": league,
            "season": "2324",
            "home_team": home,
            "away_team": away,
            "home_goals": hg,
            "away_goals": ag,
            "result": "H" if hg > ag else ("A" if hg < ag else "D"),
            "entry_home": 2.1,
            "entry_draw": e_d,
            "entry_away": 3.6,
            "entry_source": "Pinnacle (opening)",
            "close_home": 2.1,
            "close_draw": c_d,
            "close_away": 3.6,
            "close_source": "Pinnacle closing",
            "retail_draw": c_d,
        })
    return pd.DataFrame(rows)


class TestGatePipeline:
    def test_run_evidence_pack_generates_markdown_and_json(self, tmp_path):
        matches = _synthetic_matches(n_matches=250, league="E0")
        report_md = tmp_path / "gate_report.md"
        report_json = tmp_path / "gate_report.json"

        result = run_evidence_pack(
            matches=matches,
            leagues=["E0"],
            xi_by_league={"E0": 0.0065},
            min_train_matches=80,
            retrain_every_days=20,
            output_report_path=report_md,
            output_json_path=report_json,
        )

        assert report_md.exists()
        assert report_json.exists()
        assert "E0" in result["league_summaries"]
        assert result["gate_decision"]["decision"] in [
            "proceed_with_leagues", "reposition_as_forecasting_tool", "inconclusive"
        ]

        content = report_md.read_text(encoding="utf-8")
        assert "WP3 Gate Report" in content
        assert "CLV by league" in content
