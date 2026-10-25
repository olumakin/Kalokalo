from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.tracking.supabase_ledger import (
    auto_settle_predictions,
    fetch_gate_status,
    fetch_latest_predictions,
    fetch_predictions,
    fetch_predictions_since,
    fetch_settlements,
    get_supabase_client,
    high_draw_likelihood_results,
    is_ledger_online,
    latest_forecast_per_match,
    latest_settlement_by_fixture,
    ledger_export,
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
        with patch("supabase.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            get_supabase_client(url="https://explicit.example.supabase.co", key="explicit-key")
            mock_create.assert_called_once_with("https://explicit.example.supabase.co", "explicit-key")

    def test_falls_back_to_env_vars(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://env.example.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
        with patch("supabase.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            get_supabase_client()
            mock_create.assert_called_once_with("https://env.example.supabase.co", "env-key")

    def test_client_construction_failure_returns_none_not_raise(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://env.example.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
        with patch("supabase.create_client", side_effect=RuntimeError("boom")):
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


class TestFetchPredictionsSince:
    def test_no_client_returns_empty_dataframe(self):
        assert fetch_predictions_since(pd.Timestamp("2026-08-01", tz="UTC"), client=None).empty

    def test_filters_by_match_date_not_a_row_count(self):
        client = MagicMock()
        client.table().select().gte().order().limit().execute.return_value = MagicMock(data=[
            {"fixture_id": "a", "match_date": "2026-09-01"},
            {"fixture_id": "b", "match_date": "2026-10-01"},
        ])
        result = fetch_predictions_since(pd.Timestamp("2026-08-01", tz="UTC"), client=client)
        assert len(result) == 2
        client.table().select().gte.assert_called_with("match_date", "2026-08-01T00:00:00+00:00")

    def test_query_failure_returns_empty_dataframe_not_raise(self):
        client = MagicMock()
        client.table().select().gte().order().limit().execute.side_effect = RuntimeError("network blocked")
        assert fetch_predictions_since(pd.Timestamp("2026-08-01", tz="UTC"), client=client).empty


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


class TestHighDrawLikelihoodResults:
    def _predictions(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"fixture_id": "f1", "league": "E0", "home_id": "ARS", "away_id": "CHE",
             "p_draw": 0.30, "match_date": "2026-09-20"},  # above threshold
            {"fixture_id": "f2", "league": "E0", "home_id": "HUL", "away_id": "BRE",
             "p_draw": 0.32, "match_date": "2026-09-21"},  # above threshold
            {"fixture_id": "f3", "league": "E0", "home_id": "MUN", "away_id": "AVL",
             "p_draw": 0.10, "match_date": "2026-09-22"},  # below threshold -- excluded
            {"fixture_id": "f4", "league": "I1", "home_id": "MIL", "away_id": "BOL",
             "p_draw": 0.28, "match_date": "2026-09-23"},  # above threshold
            {"fixture_id": "f5", "league": "I1", "home_id": "ROM", "away_id": "NAP",
             "p_draw": 0.29, "match_date": "2026-09-24"},  # above threshold, unsettled
            {"fixture_id": "f6", "league": "E0", "home_id": "TOT", "away_id": "WHU",
             "p_draw": 0.31, "match_date": "2025-05-01"},  # above threshold, pre-season cutoff
        ])

    def _settlements(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"fixture_id": "f1", "actual_result": "D"},
            {"fixture_id": "f2", "actual_result": "H"},
            {"fixture_id": "f3", "actual_result": "D"},
            {"fixture_id": "f4", "actual_result": "D"},
            {"fixture_id": "f6", "actual_result": "D"},
            # f5 deliberately has no settlement row -- unsettled, must be excluded.
        ])

    def test_empty_predictions_returns_empty_with_expected_columns(self):
        result = high_draw_likelihood_results(pd.DataFrame(), self._settlements(), p_draw_threshold=0.275)
        assert result.empty
        assert list(result.columns) == ["match_date", "league", "home_id", "away_id", "p_draw", "actual_result", "was_draw"]

    def test_empty_settlements_returns_empty(self):
        result = high_draw_likelihood_results(self._predictions(), pd.DataFrame(), p_draw_threshold=0.275)
        assert result.empty

    def test_unsettled_fixtures_are_excluded(self):
        result = high_draw_likelihood_results(self._predictions(), self._settlements(), p_draw_threshold=0.275)
        # f5 (above threshold, no settlement row) must not appear at all.
        assert not ((result["home_id"] == "ROM") & (result["away_id"] == "NAP")).any()

    def test_below_threshold_fixtures_are_excluded(self):
        result = high_draw_likelihood_results(self._predictions(), self._settlements(), p_draw_threshold=0.275)
        # f3 (p_draw=0.10, actually a draw) must not appear -- below threshold.
        assert not ((result["home_id"] == "MUN") & (result["away_id"] == "AVL")).any()

    def test_was_draw_flag_matches_actual_result(self):
        result = high_draw_likelihood_results(self._predictions(), self._settlements(), p_draw_threshold=0.275)
        f1 = result[(result["home_id"] == "ARS") & (result["away_id"] == "CHE")].iloc[0]
        f2 = result[(result["home_id"] == "HUL") & (result["away_id"] == "BRE")].iloc[0]
        assert f1["was_draw"] == True  # noqa: E712
        assert f2["was_draw"] == False  # noqa: E712

    def test_since_filters_out_fixtures_before_the_cutoff(self):
        result = high_draw_likelihood_results(
            self._predictions(), self._settlements(), p_draw_threshold=0.275,
            since=pd.Timestamp("2026-08-01", tz="UTC"),
        )
        # f6 (2025-05-01, above threshold, settled) is before the cutoff -- excluded.
        assert not ((result["home_id"] == "TOT") & (result["away_id"] == "WHU")).any()

    def test_no_since_includes_everything_regardless_of_date(self):
        result = high_draw_likelihood_results(self._predictions(), self._settlements(), p_draw_threshold=0.275)
        assert ((result["home_id"] == "TOT") & (result["away_id"] == "WHU")).any()

    def test_no_fixture_clears_the_threshold_returns_empty(self):
        predictions = pd.DataFrame([{"fixture_id": "f1", "league": "E0", "home_id": "A", "away_id": "B",
                                      "p_draw": 0.10, "match_date": "2026-09-20"}])
        settlements = pd.DataFrame([{"fixture_id": "f1", "actual_result": "D"}])
        result = high_draw_likelihood_results(predictions, settlements, p_draw_threshold=0.275)
        assert result.empty


class TestAutoSettlePredictions:
    AS_OF = pd.Timestamp("2026-10-25", tz="UTC")

    def _predictions(self) -> pd.DataFrame:
        return pd.DataFrame([
            # Past, unsettled, has a matching recent result -- should settle.
            {"fixture_id": "f1", "league": "E0", "home_id": "ARS", "away_id": "CHE", "match_date": "2026-10-20"},
            # Already settled -- must be skipped even though a result exists.
            {"fixture_id": "f2", "league": "E0", "home_id": "HUL", "away_id": "BRE", "match_date": "2026-10-21"},
            # Past, unsettled, but no matching row in recent_results (not yet published) -- skipped.
            {"fixture_id": "f3", "league": "E0", "home_id": "MUN", "away_id": "AVL", "match_date": "2026-10-22"},
            # Not yet played (match_date in the future relative to AS_OF) -- must never settle.
            {"fixture_id": "f4", "league": "I1", "home_id": "MIL", "away_id": "BOL", "match_date": "2026-10-31"},
        ])

    def _recent_results(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"date": "2026-10-20", "league": "E0", "home_team": "ARS", "away_team": "CHE",
             "home_goals": 2, "away_goals": 2, "result": "D"},
            {"date": "2026-10-21", "league": "E0", "home_team": "HUL", "away_team": "BRE",
             "home_goals": 1, "away_goals": 0, "result": "H"},
        ])

    def test_empty_predictions_returns_empty(self):
        assert auto_settle_predictions(pd.DataFrame(), set(), self._recent_results(), as_of=self.AS_OF) == []

    def test_empty_recent_results_returns_empty(self):
        assert auto_settle_predictions(self._predictions(), set(), pd.DataFrame(), as_of=self.AS_OF) == []

    def test_matches_past_unsettled_fixture_with_a_recent_result(self):
        out = auto_settle_predictions(self._predictions(), set(), self._recent_results(), as_of=self.AS_OF)
        f1 = next(s for s in out if s["fixture_id"] == "f1")
        assert f1["home_goals"] == 2 and f1["away_goals"] == 2 and f1["actual_result"] == "D"
        assert f1["settled_by"] == "auto:football-data.co.uk"

    def test_already_settled_fixture_is_skipped(self):
        out = auto_settle_predictions(self._predictions(), {"f2"}, self._recent_results(), as_of=self.AS_OF)
        assert not any(s["fixture_id"] == "f2" for s in out)

    def test_fixture_with_no_matching_result_is_skipped(self):
        out = auto_settle_predictions(self._predictions(), set(), self._recent_results(), as_of=self.AS_OF)
        assert not any(s["fixture_id"] == "f3" for s in out)

    def test_future_fixture_is_never_settled_even_if_a_result_happens_to_match(self):
        recent_results = pd.concat([self._recent_results(), pd.DataFrame([
            {"date": "2026-10-31", "league": "I1", "home_team": "MIL", "away_team": "BOL",
             "home_goals": 1, "away_goals": 1, "result": "D"},
        ])], ignore_index=True)
        out = auto_settle_predictions(self._predictions(), set(), recent_results, as_of=self.AS_OF)
        assert not any(s["fixture_id"] == "f4" for s in out)

    def test_duplicate_prediction_rows_for_the_same_fixture_settle_once(self):
        predictions = pd.concat([self._predictions(), pd.DataFrame([
            {"fixture_id": "f1", "league": "E0", "home_id": "ARS", "away_id": "CHE", "match_date": "2026-10-20"},
        ])], ignore_index=True)
        out = auto_settle_predictions(predictions, set(), self._recent_results(), as_of=self.AS_OF)
        assert sum(1 for s in out if s["fixture_id"] == "f1") == 1


class TestLatestForecastPerMatch:
    def test_empty_returns_empty(self):
        assert latest_forecast_per_match(pd.DataFrame()).empty

    def test_keeps_only_the_most_recent_row_per_fixture(self):
        predictions = pd.DataFrame([
            {"fixture_id": "f1", "created_at": "2026-10-20T09:00:00Z", "p_draw": 0.25},
            {"fixture_id": "f1", "created_at": "2026-10-22T09:00:00Z", "p_draw": 0.30},
            {"fixture_id": "f1", "created_at": "2026-10-21T09:00:00Z", "p_draw": 0.28},
            {"fixture_id": "f2", "created_at": "2026-10-20T09:00:00Z", "p_draw": 0.20},
        ])
        result = latest_forecast_per_match(predictions)
        assert len(result) == 2
        f1 = result[result["fixture_id"] == "f1"].iloc[0]
        assert f1["p_draw"] == pytest.approx(0.30)  # the 10-22 row, not the earlier two

    def test_nine_repeats_of_one_match_collapse_to_one_row(self):
        # Mirrors the real-world pattern this was built for: a fixture
        # re-predicted on every pipeline run while it's still upcoming.
        predictions = pd.DataFrame([
            {"fixture_id": "f1", "created_at": f"2026-10-{10+i:02d}T09:00:00Z", "p_draw": 0.2 + i * 0.01}
            for i in range(9)
        ])
        result = latest_forecast_per_match(predictions)
        assert len(result) == 1


class TestLedgerExport:
    def test_merges_settled_result_onto_the_latest_forecast(self):
        predictions = pd.DataFrame([
            {"fixture_id": "f1", "created_at": "2026-10-20T09:00:00Z", "league": "E0", "p_draw": 0.30},
            {"fixture_id": "f2", "created_at": "2026-10-21T09:00:00Z", "league": "E0", "p_draw": 0.25},
        ])
        settlements = pd.DataFrame([
            {"fixture_id": "f1", "home_goals": 1, "away_goals": 1, "actual_result": "D"},
        ])
        result = ledger_export(predictions, settlements)
        assert len(result) == 2  # f2 kept even though it's unsettled
        f1 = result[result["fixture_id"] == "f1"].iloc[0]
        f2 = result[result["fixture_id"] == "f2"].iloc[0]
        assert f1["actual_result"] == "D"
        assert pd.isna(f2["actual_result"])

    def test_empty_settlements_still_returns_deduplicated_forecasts(self):
        predictions = pd.DataFrame([
            {"fixture_id": "f1", "created_at": "2026-10-20T09:00:00Z", "p_draw": 0.30},
            {"fixture_id": "f1", "created_at": "2026-10-21T09:00:00Z", "p_draw": 0.32},
        ])
        result = ledger_export(predictions, pd.DataFrame())
        assert len(result) == 1


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

    def test_window_days_populated_when_given(self):
        model = MagicMock(rho_=-0.05, converged_=True, fallback_used_=False)
        settings = {"model": {"xi_decay": 0.0065}, "edge": {}}

        out = prediction_row_from_pipeline(
            self._full_row(), run_id="run-1", model=model, settings=settings,
            price_source="live_odds", window_days=730,
        )

        assert out["window_days"] == 730

    def test_window_days_defaults_to_null(self):
        # No window trim applied -- must be NULL, not silently 0 or
        # some other value a reader could mistake for "validated".
        model = MagicMock(rho_=-0.05, converged_=True, fallback_used_=False)
        settings = {"model": {"xi_decay": 0.0065}, "edge": {}}

        out = prediction_row_from_pipeline(
            self._full_row(), run_id="run-1", model=model, settings=settings, price_source="live_odds",
        )

        assert out["window_days"] is None
