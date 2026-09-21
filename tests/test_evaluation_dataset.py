"""
Tests for A10: Historical snapshot-to-evaluation dataset assembly and pricing legs preservation.
"""
import pandas as pd
import pytest

from src.ingestion.historical import assemble_historical_evaluation_dataset
from src.ingestion.normalizer import EVALUATION_COLUMNS, normalize_evaluation_dataframe


class TestEvaluationDatasetAssembly:
    def test_normalize_evaluation_dataframe_preserves_pricing_legs(self):
        raw = pd.DataFrame([
            {
                "Date": "15/08/2023",
                "HomeTeam": "Arsenal",
                "AwayTeam": "Chelsea",
                "FTHG": 2,
                "FTAG": 1,
                "FTR": "H",
                "league": "E0",
                "season": "2324",
                "PSH": 2.05,
                "PSD": 3.40,
                "PSA": 3.80,
                "PSCH": 2.00,
                "PSCD": 3.50,
                "PSCA": 4.00,
                "B365H": 2.00,
                "B365D": 3.40,
                "B365A": 3.75,
                "AvgH": 2.02,
                "AvgD": 3.45,
                "AvgA": 3.85,
            }
        ])
        df, report = normalize_evaluation_dataframe(raw)
        assert list(df.columns) == EVALUATION_COLUMNS
        assert len(df) == 1
        assert df.iloc[0]["entry_source"] == "Pinnacle (opening)"
        assert df.iloc[0]["entry_draw"] == 3.40
        assert df.iloc[0]["close_source"] == "Pinnacle closing"
        assert df.iloc[0]["close_draw"] == 3.50
        assert df.iloc[0]["retail_draw"] == 3.40
        assert report["fully_priced_count"] == 1
        assert report["missing_entry_count"] == 0

    def test_missing_pinnacle_falls_back_to_b365_and_market_average(self):
        raw = pd.DataFrame([
            {
                "Date": "15/08/2012",
                "HomeTeam": "Arsenal",
                "AwayTeam": "Chelsea",
                "FTHG": 1,
                "FTAG": 1,
                "FTR": "D",
                "league": "E0",
                "season": "1213",
                # No PSH/PSD/PSA or PSCH/PSCD/PSCA
                "B365H": 2.10,
                "B365D": 3.25,
                "B365A": 3.60,
                "BbAvH": 2.08,
                "BbAvD": 3.20,
                "BbAvA": 3.55,
            }
        ])
        df, report = normalize_evaluation_dataframe(raw)
        assert df.iloc[0]["entry_source"] == "Bet365"
        assert df.iloc[0]["entry_draw"] == 3.25
        assert df.iloc[0]["close_source"] == "market average (legacy)"
        assert df.iloc[0]["close_draw"] == 3.20
        assert df.iloc[0]["retail_draw"] == 3.25
        assert report["fully_priced_count"] == 1

    def test_missing_price_reasons_are_counted(self):
        raw = pd.DataFrame([
            {
                "Date": "15/08/2023",
                "HomeTeam": "Arsenal",
                "AwayTeam": "Chelsea",
                "FTHG": 0,
                "FTAG": 0,
                "FTR": "D",
                "league": "E0",
                "season": "2324",
                # No entry or close odds at all
            }
        ])
        df, report = normalize_evaluation_dataframe(raw)
        assert pd.isna(df.iloc[0]["entry_draw"])
        assert pd.isna(df.iloc[0]["close_draw"])
        assert pd.isna(df.iloc[0]["retail_draw"])
        assert report["missing_entry_count"] == 1
        assert report["missing_close_count"] == 1
        assert report["missing_retail_count"] == 1
        assert report["fully_priced_count"] == 0

    def test_assemble_historical_evaluation_dataset_end_to_end(self):
        df, manifest = assemble_historical_evaluation_dataset(
            leagues=["E0"],
            seasons=["2324"],
            cache_dir="data/historical",
        )
        assert len(df) == 380
        assert "entry_draw" in df.columns
        assert "close_draw" in df.columns
        assert "retail_draw" in df.columns
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
        assert df["date"].dt.tz is not None  # UTC validated
