import pandas as pd
import pytest

from src.config import get_config
from src.engine import DomainPredictionEngine
from src.models.dixon_coles import DixonColesModel


def _synthetic_matches_by_league():
    rows = []
    start = pd.Timestamp("2024-01-01")
    for lg, teams in [("E0", ["ARS", "CHE", "LIV", "MCI"]), ("SP1", ["RMA", "BAR", "ATM", "SEV"])]:
        for day, (h, a) in enumerate([(teams[0], teams[1]), (teams[2], teams[3]), (teams[1], teams[2]), (teams[0], teams[3])] * 6):
            rows.append({
                "date": start + pd.Timedelta(days=day),
                "league": lg,
                "home_team": h,
                "away_team": a,
                "home_goals": 2 if h in ("ARS", "RMA") else 1,
                "away_goals": 1,
                "season": "2324",
            })
    return pd.DataFrame(rows)


def test_config_loads_and_validates():
    cfg = get_config()
    assert "E0" in cfg.leagues
    assert cfg.model.min_matches_for_team_rating >= 1
    assert cfg.edge.single_match_cap <= cfg.edge.daily_slate_cap


def test_domain_engine_fits_and_predicts():
    cfg = get_config()
    engine = DomainPredictionEngine(cfg)
    matches = _synthetic_matches_by_league()

    models = engine.fit_leagues(matches)
    assert "E0" in models
    assert "SP1" in models
    assert models["E0"].is_production_eligible

    fixtures = pd.DataFrame([
        {
            "date": pd.Timestamp("2024-03-01"),
            "league": "E0",
            "home_team": "ARS",
            "away_team": "CHE",
            "odds_home": 2.10,
            "odds_draw": 3.40,
            "odds_away": 3.50,
        },
        {
            "date": pd.Timestamp("2024-03-01"),
            "league": "E0",
            "home_team": "ARS",
            "away_team": "UNKNOWN_FC",
            "odds_home": 2.10,
            "odds_draw": 3.40,
            "odds_away": 3.50,
        },
    ])

    pred = engine.predict_slate(fixtures, models)
    assert len(pred) == 2
    assert pred.loc[pred["home_team"] == "ARS"].iloc[0]["status"] == "eligible"
    assert pred.loc[pred["away_team"] == "UNKNOWN_FC"].iloc[0]["status"] == "ineligible_unknown_team"


def test_pipeline_delegates_to_domain_engine(monkeypatch):
    """Verify src.pipeline.build_predictions delegates directly to DomainPredictionEngine."""
    from src.pipeline import build_predictions

    called = []
    original_predict_slate = DomainPredictionEngine.predict_slate

    def mock_predict_slate(self, fixtures, models):
        called.append(True)
        return original_predict_slate(self, fixtures, models)

    monkeypatch.setattr(DomainPredictionEngine, "predict_slate", mock_predict_slate)

    cfg = get_config()
    engine = DomainPredictionEngine(cfg)
    matches = _synthetic_matches_by_league()
    models = engine.fit_leagues(matches)

    fixtures = pd.DataFrame([
        {
            "date": pd.Timestamp("2024-03-01"),
            "league": "E0",
            "home_team": "ARS",
            "away_team": "CHE",
            "odds_home": 2.10,
            "odds_draw": 3.40,
            "odds_away": 3.50,
        }
    ])

    res = build_predictions(fixtures, models)
    assert len(called) == 1, "src.pipeline.build_predictions did not delegate to DomainPredictionEngine.predict_slate"
    assert len(res) == 1
    assert res.iloc[0]["status"] == "eligible"

