from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from scripts.migrate_local_ledger_to_supabase import (
    _match_date_from_match_id,
    _predictions_row,
    _settlement_from_old_row,
    migrate,
)


def _old_ledger_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "timestamp": pd.Timestamp("2026-01-05"), "match_id": "2026-01-04_E0_ARS_CHE",
            "league": "E0", "home_team": "ARS", "away_team": "CHE",
            "model_p_draw": 0.28, "market_p_draw": 0.25, "odds_draw": 3.4, "ev": 0.05,
            "stake_pct": 0.012, "actual_score": "1-1", "clv": 0.06, "pnl": 0.03,
        },
        {
            "timestamp": pd.Timestamp("2026-01-06"), "match_id": "2026-01-05_SP1_RMA_BAR",
            "league": "SP1", "home_team": "RMA", "away_team": "BAR",
            "model_p_draw": 0.22, "market_p_draw": 0.24, "odds_draw": 3.1, "ev": -0.02,
            "stake_pct": 0.0, "actual_score": None, "clv": None, "pnl": None,
        },
    ])


class TestMatchDateFromMatchId:
    def test_extracts_leading_date(self):
        assert _match_date_from_match_id("2026-01-04_E0_ARS_CHE") == pd.Timestamp("2026-01-04").isoformat()

    def test_none_for_missing_or_malformed(self):
        assert _match_date_from_match_id(None) is None
        assert _match_date_from_match_id("not-a-match-id") is None


class TestPredictionsRow:
    def test_maps_known_fields_and_leaves_wp3_only_fields_null(self):
        row = _old_ledger_df().iloc[0]
        out = _predictions_row(row, run_id="mig-1")

        assert out["fixture_id"] == "2026-01-04_E0_ARS_CHE"
        assert out["p_draw"] == pytest.approx(0.28)
        assert out["entry_draw"] == pytest.approx(3.4)
        assert out["stake_shadow"] == pytest.approx(0.012)
        assert out["price_source"] == "migrated_local_ledger"
        for field in ("lambda_val", "mu_val", "rho_val", "p_home", "p_away", "git_commit"):
            assert out[field] is None


class TestSettlementFromOldRow:
    def test_parses_settled_row_and_reverse_derives_closing_odds(self):
        row = _old_ledger_df().iloc[0]
        settlement = _settlement_from_old_row(row)

        assert settlement["home_goals"] == 1
        assert settlement["away_goals"] == 1
        assert settlement["actual_result"] == "D"
        # clv = odds_draw / closing - 1 = 0.06 -> closing = 3.4 / 1.06
        assert settlement["closing_odds_draw"] == pytest.approx(3.4 / 1.06)

    def test_unsettled_row_returns_none(self):
        row = _old_ledger_df().iloc[1]
        assert _settlement_from_old_row(row) is None


class TestMigrate:
    def test_missing_file_returns_zeroed_report(self, tmp_path):
        report = migrate(tmp_path / "nonexistent.parquet")
        assert report["rows_read"] == 0

    def test_no_client_marks_everything_failed(self, tmp_path):
        path = tmp_path / "ledger.csv"
        _old_ledger_df().to_csv(path, index=False)

        with patch("scripts.migrate_local_ledger_to_supabase.get_supabase_client", return_value=None):
            report = migrate(path)

        assert report["rows_read"] == 2
        assert report["predictions_failed"] == 2

    def test_full_migration_writes_predictions_and_one_settlement(self, tmp_path):
        path = tmp_path / "ledger.csv"
        _old_ledger_df().to_csv(path, index=False)
        client = MagicMock()

        with patch("scripts.migrate_local_ledger_to_supabase.get_supabase_client", return_value=client):
            report = migrate(path)

        assert report["rows_read"] == 2
        assert report["predictions_written"] == 2
        assert report["predictions_failed"] == 0
        assert report["settlements_written"] == 1  # only the settled row
        assert report["settlements_failed"] == 0
        client.table.assert_any_call("predictions")
        client.table.assert_any_call("settlements")
