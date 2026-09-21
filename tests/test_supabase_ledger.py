from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.tracking.supabase_ledger import (
    get_supabase_client,
    prediction_row_from_pipeline,
    write_predictions,
    write_settlement,
)


class TestGetSupabaseClient:
    def test_missing_credentials_returns_none(self, monkeypatch):
        import streamlit as st

        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
        # Isolate from any real .streamlit/secrets.toml in the working
        # directory (a legitimate local dev convenience elsewhere) so
        # this test verifies "no credentials anywhere", not "no env vars".
        # st.secrets is a special Secrets singleton monkeypatch can't
        # attribute-patch directly, so swap the whole module attribute.
        monkeypatch.setattr(st, "secrets", MagicMock(get=MagicMock(return_value=None)))
        assert get_supabase_client() is None

    def test_explicit_credentials_used_over_env(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://env.example.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
        with patch("supabase.create_client", create=True) as mock_create:
            mock_create.return_value = MagicMock()
            get_supabase_client(url="https://explicit.example.supabase.co", key="explicit-key")
            mock_create.assert_called_once_with("https://explicit.example.supabase.co", "explicit-key")

    def test_falls_back_to_env_vars(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://env.example.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
        with patch("supabase.create_client", create=True) as mock_create:
            mock_create.return_value = MagicMock()
            get_supabase_client()
            mock_create.assert_called_once_with("https://env.example.supabase.co", "env-key")

    def test_client_construction_failure_returns_none_not_raise(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://env.example.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
        with patch("supabase.create_client", create=True, side_effect=RuntimeError("boom")):
            assert get_supabase_client() is None


class TestWritePredictions:
    def test_no_client_marks_all_rows_failed(self):
        failed = write_predictions([{"fixture_id": "a"}, {"fixture_id": "b"}], client=None)
        assert failed == 2

    def test_empty_batch_is_a_noop(self):
        assert write_predictions([], client=MagicMock()) == 0

    def test_successful_upsert_reports_zero_failures(self):
        client = MagicMock()
        rows = [{"fixture_id": "a"}, {"fixture_id": "b"}]

        failed = write_predictions(rows, client=client)

        assert failed == 0
        client.table.assert_called_with("predictions")
        client.table().upsert.assert_called_once_with(
            rows, on_conflict="fixture_id,model_version,run_id", ignore_duplicates=True,
        )
        client.table().upsert().execute.assert_called_once()

    def test_write_failure_reports_all_rows_failed_not_raise(self):
        client = MagicMock()
        client.table().upsert().execute.side_effect = RuntimeError("network blocked")

        failed = write_predictions([{"fixture_id": "a"}, {"fixture_id": "b"}], client=client)

        assert failed == 2


class TestWriteSettlement:
    def test_no_client_returns_false(self):
        assert write_settlement("fix_1", 3.4, "D", client=None) is False

    def test_successful_insert_returns_true(self):
        client = MagicMock()
        ok = write_settlement("fix_1", 3.4, "D", client=client)
        assert ok is True
        client.table.assert_called_with("settlements")
        client.table().insert.assert_called_once_with(
            {"fixture_id": "fix_1", "odds_close": 3.4, "actual_result": "D"}
        )

    def test_insert_failure_returns_false_not_raise(self):
        client = MagicMock()
        client.table().insert().execute.side_effect = RuntimeError("network blocked")
        assert write_settlement("fix_1", 3.4, "D", client=client) is False


class TestPredictionRowFromPipeline:
    def test_maps_pipeline_row_onto_supabase_schema(self):
        row = pd.Series({
            "match_id": "2026-09-20_E0_ARS_CHE",
            "date": pd.Timestamp("2026-09-20"),
            "league": "E0",
            "home_team": "ARS",
            "away_team": "CHE",
            "model_p_home": 0.45,
            "model_p_draw": 0.28,
            "model_p_away": 0.27,
            "xg_home": 1.6,
            "xg_away": 1.1,
            "odds_draw": 3.4,
            "ev": 0.05,
        })
        model = MagicMock(rho_=-0.05, converged_=True, fallback_used_=False)
        settings = {"model": {"xi_decay": 0.0065}}

        out = prediction_row_from_pipeline(row, run_id="run-1", model=model, settings=settings, price_source="live_odds")

        assert out["fixture_id"] == "2026-09-20_E0_ARS_CHE"
        assert out["run_id"] == "run-1"
        assert out["p_draw"] == pytest.approx(0.28)
        assert out["lambda_val"] == pytest.approx(1.6)
        assert out["rho_val"] == pytest.approx(-0.05)
        assert out["converged"] is True
        assert out["skip_reason"] is None
        assert out["price_source"] == "live_odds"

    def test_fallback_model_sets_skip_reason(self):
        row = pd.Series({
            "match_id": "m1", "date": pd.Timestamp("2026-09-20"), "league": "E0",
            "home_team": "ARS", "away_team": "CHE", "model_p_home": 0.4, "model_p_draw": 0.3,
            "model_p_away": 0.3, "xg_home": 1.2, "xg_away": 1.1, "odds_draw": 3.3, "ev": 0.01,
        })
        model = MagicMock(rho_=0.0, converged_=False, fallback_used_=True)
        settings = {"model": {"xi_decay": 0.0065}}

        out = prediction_row_from_pipeline(row, run_id="run-1", model=model, settings=settings, price_source="sample_card")

        assert out["skip_reason"] == "fallback_to_independent_poisson"
        assert out["converged"] is False
