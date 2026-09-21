from fastapi.testclient import TestClient
import pytest

from api.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "E0" in data["supported_leagues"]


def test_leagues_endpoint():
    response = client.get("/api/v1/leagues")
    assert response.status_code == 200
    data = response.json()
    assert "E0" in data["leagues"]
    assert data["leagues"]["E0"] == "English Premier League"


def test_fixtures_endpoint_defaults_to_no_sample():
    response = client.get("/api/v1/fixtures?leagues=E0&allow_sample=false")
    assert response.status_code == 200
    data = response.json()
    # In sandbox without network, source must be unavailable, not sample
    assert data["source"] in ("unavailable", "live_odds", "free_schedule")


def test_diagnostics_unfitted_league_returns_404():
    response = client.get("/api/v1/diagnostics/NONEXISTENT")
    assert response.status_code == 404


def test_predict_endpoint_empty_and_unmodeled():
    # Empty list
    res_empty = client.post("/api/v1/predict", json=[])
    assert res_empty.status_code == 200
    assert res_empty.json()["count"] == 0

    # Fixture with unmodeled league returns typed ineligible status
    res = client.post("/api/v1/predict", json=[
        {
            "date": "2024-01-01",
            "league": "E0",
            "home_team": "ARS",
            "away_team": "CHE",
            "odds_home": 2.1,
            "odds_draw": 3.4,
            "odds_away": 3.6,
        }
    ])
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["predictions"][0]["status"] == "ineligible_unmodeled_league"
    assert not data["predictions"][0]["qualified"]

