"""
DVPE REST API backend service (M03 / Headless Architecture).

Decouples the quantitative Dixon-Coles prediction engine from Streamlit,
providing clean REST endpoints for frontend applications and background workers.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from pydantic import BaseModel, Field

from src.config import get_config
from src.engine import DomainPredictionEngine
from src.ingestion.odds_feed import get_upcoming_fixtures
from src.models.dixon_coles import DixonColesModel
from src.tracking.ledger import Ledger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Draw Value Prediction Engine (DVPE) API",
    version="1.0.0",
    description="Quantitative Dixon-Coles goal model & EV draw prediction service",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory fitted models registry
_FITTED_MODELS: dict[str, DixonColesModel] = {}


class FixtureItem(BaseModel):
    date: str
    league: str
    home_team: str
    away_team: str
    odds_home: float = Field(gt=1.0)
    odds_draw: float = Field(gt=1.0)
    odds_away: float = Field(gt=1.0)


class PredictionResponseItem(BaseModel):
    match_id: str
    date: str
    league: str
    home_team: str
    away_team: str
    xg_home: float | None = None
    xg_away: float | None = None
    model_p_draw: float | None = None
    odds_draw: float
    ev: float | None = None
    qualified: bool
    stake_pct: float
    status: str


@app.get("/api/v1/health")
def health_check():
    """System health, configuration status, and fitted models inventory."""
    config = get_config()
    return {
        "status": "healthy",
        "version": "1.0.0",
        "supported_leagues": list(config.leagues.keys()),
        "fitted_leagues": list(_FITTED_MODELS.keys()),
        "min_ev": config.edge.min_ev,
        "kelly_fraction": config.edge.kelly_fraction,
    }


@app.get("/api/v1/leagues")
def list_leagues():
    """List supported Big 5 leagues."""
    config = get_config()
    return {"leagues": config.leagues}


@app.get("/api/v1/fixtures")
def get_fixtures(
    leagues: str = Query("E0,SP1,I1,D1,F1", description="Comma-separated league codes"),
    allow_sample: bool = Query(False, description="Allow sample card if live feeds unavailable (dev only)"),
):
    """Retrieve upcoming fixtures through verified ingestion feeds."""
    league_list = [lg.strip() for lg in leagues.split(",") if lg.strip()]
    fixtures_df, source = get_upcoming_fixtures(league_list, allow_sample=allow_sample)
    return {
        "source": source,
        "count": len(fixtures_df),
        "fixtures": fixtures_df.to_dict(orient="records"),
    }


@app.post("/api/v1/predict")
def predict_fixtures(fixtures: list[FixtureItem]):
    """Score a list of upcoming fixtures through the shared domain engine (M01, M03)."""
    if not fixtures:
        return {"count": 0, "predictions": []}
    df = pd.DataFrame([f.model_dump() for f in fixtures])
    engine = DomainPredictionEngine()
    preds_df = engine.predict_slate(df, _FITTED_MODELS)
    return {
        "count": len(preds_df),
        "predictions": preds_df.to_dict(orient="records"),
    }


@app.get("/api/v1/diagnostics/{league}")
def get_league_diagnostics(league: str):
    """Retrieve fitted hyperparameters and ratings for a specific league."""
    model = _FITTED_MODELS.get(league)
    if not model:
        raise HTTPException(status_code=404, detail=f"No fitted model found for league {league}")

    return {
        "league": league,
        "converged": model.converged_,
        "fallback_used": model.fallback_used_,
        "is_production_eligible": model.is_production_eligible,
        "mu0": model.mu0_,
        "gamma": model.gamma_,
        "rho": model.rho_,
        "teams_count": len(model.teams_),
        "attack_ratings": model.alpha_,
        "defense_ratings": model.beta_,
    }


@app.get("/api/v1/ledger")
def get_ledger_records(limit: int = Query(50, ge=1, le=500)):
    """Retrieve recent prediction audit trail records from local ledger."""
    config = get_config()
    ledger = Ledger(config.ledger.path, fmt=config.ledger.format)
    df = ledger.load()
    if df.empty:
        return {"total": 0, "records": []}
    recent = df.tail(limit).to_dict(orient="records")
    return {"total": len(df), "records": recent}
