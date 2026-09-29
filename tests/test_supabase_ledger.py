from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.tracking.supabase_ledger import (
    fetch_gate_status,
    fetch_latest_predictions,
    fetch_predictions,
    fetch_settlements,
    get_supabase_client,
    is_ledger_online,
    latest_settlement_by_fixture,
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
        monkeypatch.delenv("SUPABASE_APP_SECRET", raising=False)
        with patch("supabase.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            get_supabase_client(url="https://explicit.example.supabase.co", key="explicit-key")
            args, kwargs = mock_create.call_args
            assert args == ("https://explicit.example.supabase.co", "explicit-key")
            assert "options" in kwargs

    def test_falls_back_to_env_vars(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://env.example.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
        monkeypatch.delenv("SUPABASE_APP_SECRET", raising=False)
        with patch("supabase.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            get_supabase_client()
            args, kwargs = mock_create.call_args
            assert args == ("https://env.example.supabase.co", "env-key")
            assert "options" in kwargs

    def test_client_construction_failure_returns_none_not_raise(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://env.example.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
        with patch("supabase.create_client", side_effect=RuntimeError("boom")):
            assert get_supabase_client() is None

    def test_app_secret_attached_as_header_when_configured(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://env.example.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
        monkeypatch.setenv("SUPABASE_APP_SECRET", "top-secret")
        with patch("supabase.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            get_supabase_client()
            _, kwargs = mock_create.call_args
            assert kwargs["options"].headers.get("x-app-secret") == "top-secret"

    def test_missing_app_secret_degrades_gracefully_no_header(self, monkeypatch):
        import streamlit as st

        monkeypatch.setenv("SUPABASE_URL", "https://env.example.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
        monkeypatch.delenv("SUPABASE_APP_SECRET", raising=False)
        monkeypatch.setattr(st, "secrets", MagicMock(get=MagicMock(return_value=None)))
        with patch("supabase.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            get_supabase_client()
            _, kwargs = mock_create.call_args
            assert "x-app-secret" not in kwargs["options"].headers


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
        assert write_settlement("fix_1", 1, 1, "D", client=None) is False

    def test_successful_insert_returns_true(self):
        client = MagicMock()
        ok = write_settlement(
            "fix_1", 1, 1, "D", closing_odds_draw=3.4, close_source="Pinnacle closing",
            settled_by="admin@example.com", client=client,
        )
        assert ok is True
        client.table.assert_called_with("settlements")
        client.table().insert.assert_called_once_with({
            "fixture_id": "fix_1", "home_goals": 1, "away_goals": 1,
            "closing_odds_draw": 3.4, "close_source": "Pinnacle closing",
            "actual_result": "D", "settled_by": "admin@example.com",
        })

    def test_never_touches_predictions_table(self):
        client = MagicMock()
        write_settlement("fix_1", 1, 1, "D", client=client)
        called_tables = {call.args[0] for call in client.table.call_args_list}
        assert called_tables == {"settlements"}

    def test_insert_failure_returns_false_not_raise(self):
        client = MagicMock()
        client.table().insert().execute.side_effect = RuntimeError("network blocked")
        assert write_settlement("fix_1", 1, 1, "D", client=client) is False


class TestIsLedgerOnline:
    def test_no_client_returns_false(self):
        assert is_ledger_online(client=None) is False

    def test_successful_probe_returns_true(self):
        client = MagicMock()
        assert is_ledger_online(client=client) is True
        client.table.assert_called_with("gate_status")

    def test_probe_failure_returns_false_not_raise(self):
        client = MagicMock()
        client.table().select().limit().execute.side_effect = RuntimeError("network blocked")
        assert is_ledger_online(client=client) is False


class TestFetchPredictions:
    def test_no_client_returns_empty_dataframe(self):
        assert fetch_predictions(client=None).empty

    def test_returns_all_rows_unfiltered_by_run_id(self):
        client = MagicMock()
        client.table().select().order().limit().execute.return_value = MagicMock(data=[
            {"fixture_id": "a", "run_id": "run-2"},
            {"fixture_id": "c", "run_id": "run-1"},
        ])
        result = fetch_predictions(client=client)
        assert len(result) == 2

    def test_query_failure_returns_empty_dataframe_not_raise(self):
        client = MagicMock()
        client.table().select().order().limit().execute.side_effect = RuntimeError("network blocked")
        assert fetch_predictions(client=client).empty


class TestFetchLatestPredictions:
    """fetch_latest_predictions is two queries now: a 1-row lookup of
    the most recent run_id, then a narrow-column fetch of just that
    run's rows -- select() is called with different arguments for each
    (a single "run_id" string vs. the full comma-joined column list),
    so tests key the mocked response off which columns were asked for
    rather than assuming one flat call chain."""

    @staticmethod
    def _client(latest_run_rows, run_rows):
        client = MagicMock()

        def select_side_effect(columns):
            m = MagicMock()
            if columns == "run_id":
                m.order.return_value.limit.return_value.execute.return_value = MagicMock(data=latest_run_rows)
            else:
                m.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=run_rows)
            return m

        client.table.return_value.select.side_effect = select_side_effect
        return client

    def test_no_client_returns_empty_dataframe(self):
        result = fetch_latest_predictions(client=None)
        assert result.empty

    def test_filters_to_the_most_recent_run_id(self):
        client = self._client(
            latest_run_rows=[{"run_id": "run-2"}],
            run_rows=[
                {"fixture_id": "a", "run_id": "run-2"},
                {"fixture_id": "b", "run_id": "run-2"},
            ],
        )

        result = fetch_latest_predictions(client=client)

        assert len(result) == 2
        assert set(result["fixture_id"]) == {"a", "b"}

    def test_no_predictions_at_all_returns_empty_dataframe(self):
        client = self._client(latest_run_rows=[], run_rows=[])
        assert fetch_latest_predictions(client=client).empty

    def test_latest_run_lookup_failure_returns_empty_dataframe_not_raise(self):
        client = MagicMock()
        client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.side_effect = (
            RuntimeError("network blocked")
        )
        assert fetch_latest_predictions(client=client).empty

    def test_row_fetch_failure_returns_empty_dataframe_not_raise(self):
        client = self._client(latest_run_rows=[{"run_id": "run-2"}], run_rows=[])
        client.table.return_value.select.side_effect = None

        def select_side_effect(columns):
            m = MagicMock()
            if columns == "run_id":
                m.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[{"run_id": "run-2"}])
            else:
                m.eq.return_value.limit.return_value.execute.side_effect = RuntimeError("network blocked")
            return m

        client.table.return_value.select.side_effect = select_side_effect
        assert fetch_latest_predictions(client=client).empty


class TestFetchGateStatus:
    def test_no_client_returns_empty_dict(self):
        assert fetch_gate_status(client=None) == {}

    def test_keys_by_league_code(self):
        client = MagicMock()
        client.table().select().execute.return_value = MagicMock(data=[
            {"league_code": "E0", "status": "inconclusive", "approved_at": None},
            {"league_code": "SP1", "status": "proceed", "approved_at": "2026-09-20T00:00:00Z"},
        ])

        result = fetch_gate_status(client=client)

        assert set(result.keys()) == {"E0", "SP1"}
        assert result["SP1"]["status"] == "proceed"

    def test_query_failure_returns_empty_dict_not_raise(self):
        client = MagicMock()
        client.table().select().execute.side_effect = RuntimeError("network blocked")
        assert fetch_gate_status(client=client) == {}


class TestFetchSettlements:
    def test_no_client_returns_empty_dataframe(self):
        assert fetch_settlements(client=None).empty

    def test_returns_all_rows_unfiltered(self):
        client = MagicMock()
        client.table().select().order().limit().execute.return_value = MagicMock(data=[
            {"fixture_id": "a", "settled_at": "2026-09-20T12:00:00Z"},
            {"fixture_id": "a", "settled_at": "2026-09-19T12:00:00Z"},
        ])
        result = fetch_settlements(client=client)
        assert len(result) == 2

    def test_query_failure_returns_empty_dataframe_not_raise(self):
        client = MagicMock()
        client.table().select().order().limit().execute.side_effect = RuntimeError("network blocked")
        assert fetch_settlements(client=client).empty


class TestLatestSettlementByFixture:
    def test_no_settlements_returns_empty_dataframe(self):
        client = MagicMock()
        client.table().select().order().limit().execute.return_value = MagicMock(data=[])
        assert latest_settlement_by_fixture(client=client).empty

    def test_keeps_only_the_most_recent_row_per_fixture(self):
        client = MagicMock()
        client.table().select().order().limit().execute.return_value = MagicMock(data=[
            {"fixture_id": "a", "settled_at": "2026-09-19T12:00:00Z", "home_goals": 0},
            {"fixture_id": "a", "settled_at": "2026-09-20T12:00:00Z", "home_goals": 1},
            {"fixture_id": "b", "settled_at": "2026-09-20T09:00:00Z", "home_goals": 2},
        ])

        result = latest_settlement_by_fixture(client=client)

        assert len(result) == 2
        row_a = result[result["fixture_id"] == "a"].iloc[0]
        assert row_a["home_goals"] == 1  # the later (corrective) settlement, not the first


class TestPredictionRowFromPipeline:
    def _full_row(self, **overrides) -> pd.Series:
        base = {
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
            "odds_home": 2.0,
            "odds_draw": 3.4,
            "odds_away": 3.9,
            "market_p_home": 0.42,
            "market_p_draw": 0.27,
            "market_p_away": 0.31,
            "ev": 0.05,
            "stake_pct": 0.012,
        }
        base.update(overrides)
        return pd.Series(base)

    def test_maps_pipeline_row_onto_supabase_schema(self):
        model = MagicMock(rho_=-0.05, converged_=True, fallback_used_=False)
        settings = {"model": {"xi_decay": 0.0065}, "edge": {}}

        out = prediction_row_from_pipeline(
            self._full_row(), run_id="run-1", model=model, settings=settings, price_source="live_odds",
        )

        assert out["fixture_id"] == "2026-09-20_E0_ARS_CHE"
        assert out["run_id"] == "run-1"
        assert out["p_draw"] == pytest.approx(0.28)
        assert out["lambda_val"] == pytest.approx(1.6)
        assert out["rho_val"] == pytest.approx(-0.05)
        assert out["converged"] is True
        assert out["fallback_used"] is False
        assert out["skip_reason"] is None
        assert out["price_source"] == "live_odds"
        assert isinstance(out["git_commit"], str) and len(out["git_commit"]) > 0

    def test_fallback_model_sets_skip_reason(self):
        model = MagicMock(rho_=0.0, converged_=False, fallback_used_=True)
        settings = {"model": {"xi_decay": 0.0065}, "edge": {}}

        out = prediction_row_from_pipeline(
            self._full_row(match_id="m1"), run_id="run-1", model=model, settings=settings, price_source="sample_card",
        )

        assert out["skip_reason"] == "fallback_to_independent_poisson"
        assert out["converged"] is False
        assert out["fallback_used"] is True

    def test_entry_and_market_three_way_fields_populated(self):
        model = MagicMock(rho_=-0.05, converged_=True, fallback_used_=False)
        settings = {"model": {"xi_decay": 0.0065}, "edge": {}}

        out = prediction_row_from_pipeline(
            self._full_row(), run_id="run-1", model=model, settings=settings, price_source="live_odds",
        )

        assert out["entry_home"] == pytest.approx(2.0)
        assert out["entry_draw"] == pytest.approx(3.4)
        assert out["entry_away"] == pytest.approx(3.9)
        assert out["entry_source"] == "live_odds"
        assert out["market_p_home"] == pytest.approx(0.42)
        assert out["market_p_away"] == pytest.approx(0.31)

    def test_close_and_actual_outcome_fields_always_null(self):
        model = MagicMock(rho_=-0.05, converged_=True, fallback_used_=False)
        settings = {"model": {"xi_decay": 0.0065}, "edge": {}}

        out = prediction_row_from_pipeline(
            self._full_row(), run_id="run-1", model=model, settings=settings, price_source="live_odds",
        )

        for field in ("close_home", "close_draw", "close_away", "close_source",
                      "retail_draw", "actual_home_goals", "actual_away_goals", "actual_outcome_idx"):
            assert out[field] is None

    def test_stake_shadow_and_bankroll_at_slate(self):
        model = MagicMock(rho_=-0.05, converged_=True, fallback_used_=False)
        settings = {"model": {"xi_decay": 0.0065}, "edge": {"shadow_bankroll_units": 2.5}}

        out = prediction_row_from_pipeline(
            self._full_row(), run_id="run-1", model=model, settings=settings, price_source="live_odds",
        )

        assert out["stake_shadow"] == pytest.approx(0.012)
        assert out["bankroll_at_slate"] == pytest.approx(2.5)
