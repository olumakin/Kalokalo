import pandas as pd

from src.ingestion.historical import current_season_code, current_season_start


class TestCurrentSeasonStart:
    def test_mid_season_date_in_august_onward(self):
        assert current_season_start(pd.Timestamp("2026-10-25", tz="UTC")) == pd.Timestamp("2026-08-01", tz="UTC")

    def test_date_in_july_is_still_the_prior_season(self):
        assert current_season_start(pd.Timestamp("2027-07-31", tz="UTC")) == pd.Timestamp("2026-08-01", tz="UTC")


class TestCurrentSeasonCode:
    def test_mid_season_date_in_august_onward(self):
        assert current_season_code(pd.Timestamp("2026-10-25", tz="UTC")) == "2627"

    def test_date_in_august_itself_is_the_new_season(self):
        assert current_season_code(pd.Timestamp("2026-08-01", tz="UTC")) == "2627"

    def test_date_in_july_is_still_the_prior_season(self):
        assert current_season_code(pd.Timestamp("2027-07-31", tz="UTC")) == "2627"

    def test_date_in_january_is_the_season_that_started_the_prior_august(self):
        assert current_season_code(pd.Timestamp("2027-01-15", tz="UTC")) == "2627"
