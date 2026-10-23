import pandas as pd

from src.ingestion.promotion import preceding_season, promoted_teams_by_season


def _match(league, season, home, away):
    return {
        "date": pd.Timestamp("2024-01-01"), "league": league, "season": season,
        "home_team": home, "away_team": away, "home_goals": 1, "away_goals": 1, "result": "D",
    }


class TestPrecedingSeason:
    def test_simple_case(self):
        assert preceding_season("2324") == "2223"

    def test_century_rollover(self):
        assert preceding_season("0001") == "9900"

    def test_round_trips_through_a_decade_boundary(self):
        assert preceding_season("3031") == "2930"


class TestPromotedTeamsBySeason:
    def test_team_absent_from_preceding_season_is_promoted(self):
        matches = pd.DataFrame([
            _match("E0", "2223", "A", "B"),
            _match("E0", "2324", "A", "C"),  # C is new this season
        ])
        promoted = promoted_teams_by_season(matches)
        assert promoted[("E0", "2324")] == {"C"}

    def test_team_present_in_preceding_season_is_not_promoted(self):
        matches = pd.DataFrame([
            _match("E0", "2223", "A", "B"),
            _match("E0", "2324", "A", "B"),
        ])
        promoted = promoted_teams_by_season(matches)
        assert promoted[("E0", "2324")] == set()

    def test_first_season_in_corpus_has_no_promoted_teams(self):
        # No preceding season exists in the data at all -- there's no
        # baseline to call anyone "new" against, so nobody is labeled
        # promoted, even though every team here is technically new to
        # *this corpus*.
        matches = pd.DataFrame([_match("E0", "2223", "A", "B")])
        promoted = promoted_teams_by_season(matches)
        assert promoted[("E0", "2223")] == set()

    def test_gap_in_the_corpus_is_treated_like_a_missing_preceding_season(self):
        # 2223 is missing entirely (a download gap, not "team C is new") --
        # 2324 must not treat every one of its teams as promoted just
        # because we have no 2223 data to compare against.
        matches = pd.DataFrame([
            _match("E0", "2122", "A", "B"),
            _match("E0", "2324", "A", "C"),
        ])
        promoted = promoted_teams_by_season(matches)
        assert promoted[("E0", "2324")] == set()

    def test_leagues_do_not_leak_into_each_other(self):
        matches = pd.DataFrame([
            _match("E0", "2223", "A", "B"),
            _match("E0", "2324", "A", "B"),
            _match("D1", "2223", "X", "Y"),
            _match("D1", "2324", "X", "Z"),  # Z is new in D1 only
        ])
        promoted = promoted_teams_by_season(matches)
        assert promoted[("E0", "2324")] == set()
        assert promoted[("D1", "2324")] == {"Z"}

    def test_relegated_then_promoted_team_is_detected_again(self):
        # A team absent for exactly one season (relegated, then
        # promoted straight back) is "promoted" again the season it
        # reappears -- the proxy only ever looks at the immediately
        # preceding season.
        matches = pd.DataFrame([
            _match("E0", "2122", "A", "B"),
            _match("E0", "2223", "A", "C"),  # B relegated
            _match("E0", "2324", "A", "B"),  # B promoted back
        ])
        promoted = promoted_teams_by_season(matches)
        assert promoted[("E0", "2223")] == {"C"}
        assert promoted[("E0", "2324")] == {"B"}

    def test_multiple_promoted_teams_in_one_season(self):
        # 2324's roster (C, D) shares nothing with 2223's (A, B) -- both
        # of 2324's teams are promoted.
        matches = pd.DataFrame([
            _match("E0", "2223", "A", "B"),
            _match("E0", "2324", "C", "D"),
        ])
        promoted = promoted_teams_by_season(matches)
        assert promoted[("E0", "2324")] == {"C", "D"}

    def test_empty_matches_returns_empty_dict(self):
        assert promoted_teams_by_season(pd.DataFrame()) == {}
