"""
Focused test suite for Stage 2: Authoritative Fixture Tracker and Event-Driven Lifecycle.
"""
from datetime import datetime, timezone

import pytest

from src.ingestion.fixture_tracker import (
    ActualKickoffStatus,
    FixtureLifecycleState,
    FixtureTracker,
    IdentityConflictError,
    IllegalStateTransitionError,
    IngestDisposition,
    ReconciliationRequiredError,
    generate_internal_fixture_id,
    rebuild_projection_from_events,
)
from src.tracking.events import ImmutableEventStore, TestFlag


def test_fixture_internal_id_stable_across_reschedule():
    """Kickoff-time changes must not alter the internal canonical fixture ID."""
    id1 = generate_internal_fixture_id("E0", "2324", "ARS", "CHE")
    # Date shifts by days or months within the same competition/season
    id2 = generate_internal_fixture_id("E0", "2324", "ARS", "CHE")
    assert id1 == id2
    assert id1.startswith("fix_e0_")


def test_provider_replay_idempotent():
    """Identical provider observations replayed must be flagged as IDEMPOTENT_REPLAY."""
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    res1 = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_odds_001",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    assert res1.disposition == IngestDisposition.VALID_LIFECYCLE_UPDATE

    # Replay exact same observation
    res2 = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_odds_001",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    assert res2.disposition == IngestDisposition.IDEMPOTENT_REPLAY
    assert res1.internal_fixture_id == res2.internal_fixture_id


def test_provider_id_collision_detected():
    """Reusing the same provider event ID for different teams or competition must raise IdentityConflictError."""
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_shared_123",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
    )

    # Corrupted feed reuses 'evt_shared_123' for LIV vs MCI
    with pytest.raises(IdentityConflictError, match="already bound to.*conflicts with incoming"):
        tracker.process_provider_event(
            provider_namespace="the_odds_api",
            provider_event_id="evt_shared_123",
            competition_id="E0",
            season_id="2627",
            home_team_id="LIV",
            away_team_id="MCI",
            scheduled_kickoff_utc=k_time,
        )


def test_same_matchup_distinct_fixtures_unique_ids():
    """Item 1: Same comp, season, home, away, two distinct fixtures must get distinct internal IDs from registry."""
    tracker = FixtureTracker()
    k1 = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
    k2 = datetime(2027, 2, 15, 20, 0, tzinfo=timezone.utc)

    # Fixture 1: e.g. First domestic meeting
    res1 = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_leg_1",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
    )

    # Fixture 2: e.g. Second distinct meeting in same season (cup replay or split fixture)
    res2 = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_leg_2",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        create_new_fixture=True,
    )

    assert res1.internal_fixture_id != res2.internal_fixture_id
    assert res1.internal_fixture_id.startswith("fix_e0_")
    assert res2.internal_fixture_id.startswith("fix_e0_")

    # Both resolve unambiguously to their respective distinct internal fixtures
    assert tracker.resolve_by_provider_id("the_odds_api", "evt_leg_1") == res1.internal_fixture_id
    assert tracker.resolve_by_provider_id("the_odds_api", "evt_leg_2") == res2.internal_fixture_id

    # Registry tracks both occurrences
    nat_key = ("E0", "2627", "ARS", "CHE")
    assert len(tracker._fixtures_by_natural_key[nat_key]) == 2


def test_provider_id_replacement_can_alias_same_fixture():
    """Item 2: Provider supplying new event ID after rescheduling links cleanly to existing fixture; aliases preserved."""
    tracker = FixtureTracker()
    k1 = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)
    k2 = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)

    # 1. Initial fixture registered under provider ID A
    res1 = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_orig_100",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )

    # 2. Postponed
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_orig_100",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
        reported_state=FixtureLifecycleState.POSTPONED,
    )

    # 3. Provider republishes same real-world fixture using provider ID B
    res_resched = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_new_200",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        reported_state=FixtureLifecycleState.RESCHEDULED,
        allow_alias_linkage=True,
    )

    # 4 & 5. Both provider IDs must resolve to the same internal fixture ID
    assert res_resched.internal_fixture_id == res1.internal_fixture_id
    assert tracker.resolve_by_provider_id("the_odds_api", "evt_orig_100") == res1.internal_fixture_id
    assert tracker.resolve_by_provider_id("the_odds_api", "evt_new_200") == res1.internal_fixture_id

    # 6. Original alias must remain preserved
    proj = tracker.get_projection(res1.internal_fixture_id)
    assert proj.current_state == FixtureLifecycleState.RESCHEDULED
    assert proj.latest_scheduled_kickoff_utc == k2
    assert "evt_orig_100" in proj.provider_aliases["the_odds_api"]
    assert "evt_new_200" in proj.provider_aliases["the_odds_api"]


def test_ambiguous_reconciliation_fails_closed():
    """Item 3: When multiple active fixtures match or alias linkage is disabled, tracker fails closed with FIXTURE_RECONCILIATION_REQUIRED."""
    tracker = FixtureTracker()
    k1 = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)
    k2 = datetime(2027, 1, 10, 15, 0, tzinfo=timezone.utc)

    # Scenario A: Disabled alias linkage on single match fails closed
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_first",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
    )

    with pytest.raises(ReconciliationRequiredError) as exc_info:
        tracker.process_provider_event(
            provider_namespace="the_odds_api",
            provider_event_id="evt_second",
            competition_id="E0",
            season_id="2627",
            home_team_id="ARS",
            away_team_id="CHE",
            scheduled_kickoff_utc=k1,
            allow_alias_linkage=False,
        )
    assert exc_info.value.disposition == IngestDisposition.RECONCILIATION_REQUIRED

    # Scenario B: Multiple active candidate fixtures exist -> ambiguous reconciliation fails closed even if allow_alias_linkage=True
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_cup_leg",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        create_new_fixture=True,
    )

    with pytest.raises(ReconciliationRequiredError, match="Ambiguous reconciliation") as exc_info_mult:
        tracker.process_provider_event(
            provider_namespace="pinnacle",
            provider_event_id="evt_unclear_pin",
            competition_id="E0",
            season_id="2627",
            home_team_id="ARS",
            away_team_id="CHE",
            scheduled_kickoff_utc=k1,
            allow_alias_linkage=True,
        )
    assert exc_info_mult.value.disposition == IngestDisposition.RECONCILIATION_REQUIRED


def test_invalid_state_transition_rejected():
    """Item 10: Impossible state transitions must be rejected fail-closed."""
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    res = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_trans_01",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )

    # SCHEDULED -> COMPLETED directly without STARTED is illegal
    with pytest.raises(IllegalStateTransitionError, match="Illegal fixture lifecycle transition"):
        tracker.process_provider_event(
            provider_namespace="the_odds_api",
            provider_event_id="evt_trans_01",
            competition_id="E0",
            season_id="2627",
            home_team_id="ARS",
            away_team_id="CHE",
            scheduled_kickoff_utc=k_time,
            reported_state=FixtureLifecycleState.COMPLETED,
        )


def test_valid_postponement_reschedule_sequence():
    """Item 10: Verify clean full lifecycle transition: SCHEDULED -> POSTPONED -> RESCHEDULED -> STARTED -> COMPLETED -> OFFICIAL_RESULT_STANDS."""
    tracker = FixtureTracker()
    k1 = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)
    k2 = datetime(2026, 10, 15, 19, 45, tzinfo=timezone.utc)

    # 1. SCHEDULED
    res = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_seq",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    fid = res.internal_fixture_id

    # 2. POSTPONED
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_seq",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
        reported_state=FixtureLifecycleState.POSTPONED,
    )

    # 3. RESCHEDULED
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_seq",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        reported_state=FixtureLifecycleState.RESCHEDULED,
    )

    # 4. STARTED
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_seq",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        reported_state=FixtureLifecycleState.STARTED,
        actual_kickoff_utc=k2,
        actual_kickoff_status=ActualKickoffStatus.PROVIDER_REPORTED,
    )

    # 5. COMPLETED
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_seq",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        reported_state=FixtureLifecycleState.COMPLETED,
    )

    # 6. OFFICIAL_RESULT_STANDS
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_seq",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        reported_state=FixtureLifecycleState.OFFICIAL_RESULT_STANDS,
    )

    proj = tracker.get_projection(fid)
    assert proj.current_state == FixtureLifecycleState.OFFICIAL_RESULT_STANDS
    assert len(proj.schedule_history) == 1
    assert proj.original_scheduled_kickoff_utc == k1
    assert proj.latest_scheduled_kickoff_utc == k2
    assert proj.actual_kickoff_utc == k2


def test_abandoned_match_waits_for_ruling():
    """Item 10: An abandoned match transitions to ABANDONED_PENDING_RULING until official ruling."""
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_abn",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )

    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_abn",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.STARTED,
    )

    # Weather abandonment at 60 mins
    res = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_abn",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.ABANDONED_PENDING_RULING,
        reason="Severe waterlogging at 60th minute",
    )

    proj = tracker.get_projection(res.internal_fixture_id)
    assert proj.current_state == FixtureLifecycleState.ABANDONED_PENDING_RULING

    # Governing body ratifies result
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_abn",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.OFFICIAL_RESULT_STANDS,
        reason="Premier League confirmed scoreline stands",
    )
    assert tracker.get_projection(res.internal_fixture_id).current_state == FixtureLifecycleState.OFFICIAL_RESULT_STANDS


def test_voided_fixture_terminal():
    """Item 10: A VOIDED fixture is terminal and cannot transition forward."""
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_void",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )

    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_void",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.VOIDED,
    )

    with pytest.raises(IllegalStateTransitionError):
        tracker.process_provider_event(
            provider_namespace="the_odds_api",
            provider_event_id="evt_void",
            competition_id="E0",
            season_id="2627",
            home_team_id="ARS",
            away_team_id="CHE",
            scheduled_kickoff_utc=k_time,
            reported_state=FixtureLifecycleState.STARTED,
        )


def test_actual_kickoff_never_inferred_from_schedule():
    """Item 10: Actual kickoff must remain None unless explicitly reported with status."""
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    res = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_no_infer",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )

    proj = tracker.get_projection(res.internal_fixture_id)
    assert proj.actual_kickoff_utc is None
    assert proj.actual_kickoff_status == ActualKickoffStatus.UNKNOWN


def test_cross_league_fixture_collision_blocked():
    """Item 4 & 10: Competition identity isolates fixtures; provider events cannot silently migrate across leagues."""
    id_monaco = generate_internal_fixture_id("F1", "2324", "MON", "PSG")
    id_monza = generate_internal_fixture_id("I1", "2324", "MNZ", "JUV")

    assert id_monaco != id_monza
    assert id_monaco.startswith("fix_f1_")
    assert id_monza.startswith("fix_i1_")

    # Identical team codes in different competitions do not collide
    id_comp_a = generate_internal_fixture_id("E0", "2627", "LIV", "EVE")
    id_comp_b = generate_internal_fixture_id("CL", "2627", "LIV", "EVE")
    assert id_comp_a != id_comp_b

    # Provider event migration across competitions is blocked
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_cross_1",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
    )

    with pytest.raises(IdentityConflictError, match="already bound to.*conflicts with incoming"):
        tracker.process_provider_event(
            provider_namespace="the_odds_api",
            provider_event_id="evt_cross_1",
            competition_id="F1",
            season_id="2627",
            home_team_id="ARS",
            away_team_id="CHE",
            scheduled_kickoff_utc=k_time,
        )


def test_team_display_name_does_not_define_identity():
    """Item 5 & 10: Display name variations, formatting, accents, or labels do not alter internal fixture identity."""
    id_clean = generate_internal_fixture_id("E0", "2324", "ARS", "CHE")
    id_case_variant = generate_internal_fixture_id("e0 ", " 2324 ", "ars", "che")
    assert id_clean == id_case_variant

    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    res1 = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_name_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        home_display_name="Arsenal FC",
        away_display_name="Chelsea FC",
        scheduled_kickoff_utc=k_time,
    )

    # Re-observed with distinct unicode display names but identical IDs
    res2 = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_name_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        home_display_name="Ársenal Football Club",
        away_display_name="Chélsea London",
        scheduled_kickoff_utc=k_time,
    )

    assert res1.internal_fixture_id == res2.internal_fixture_id
    assert res2.disposition == IngestDisposition.IDEMPOTENT_REPLAY


def test_lifecycle_events_are_append_only():
    """Item 10: Multiple state updates on a fixture must append discrete immutable events without mutating prior events."""
    store = ImmutableEventStore()
    tracker = FixtureTracker(event_store=store)
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_append",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_append",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.STARTED,
        actual_kickoff_utc=k_time,
        actual_kickoff_status=ActualKickoffStatus.PROVIDER_REPORTED,
    )

    # 2 distinct events in store
    events = tracker.get_events_for_fixture(tracker.resolve_by_provider_id("the_odds_api", "evt_append"))
    assert len(events) == 2
    assert events[0].payload["new_state"] == "SCHEDULED"
    assert events[1].payload["new_state"] == "STARTED"
    assert events[0].payload["previous_state"] is None
    assert events[1].payload["previous_state"] == "SCHEDULED"
    assert events[0].payload["fixture_event_sequence"] == 1
    assert events[1].payload["fixture_event_sequence"] == 2


def test_projection_rebuild_is_deterministic():
    """Item 10 & 9: Replaying events sequentially reconstructs exact projection state deterministically."""
    store = ImmutableEventStore()
    tracker = FixtureTracker(event_store=store)
    k1 = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)
    k2 = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)

    res = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_replay_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_replay_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        reported_state=FixtureLifecycleState.RESCHEDULED,
    )

    original_proj = tracker.get_projection(res.internal_fixture_id)
    raw_events = tracker.get_events_for_fixture(res.internal_fixture_id)

    reconstructed_proj = rebuild_projection_from_events(raw_events)
    assert reconstructed_proj.internal_fixture_id == original_proj.internal_fixture_id
    assert reconstructed_proj.current_state == original_proj.current_state
    assert reconstructed_proj.latest_scheduled_kickoff_utc == original_proj.latest_scheduled_kickoff_utc
    assert len(reconstructed_proj.schedule_history) == len(original_proj.schedule_history)


def test_stage2_events_are_non_prospective():
    """Item 6 & 10: All Stage 2 events must carry TEST_ONLY_NON_PROSPECTIVE test flag and never increment prospective denominator."""
    store = ImmutableEventStore()
    tracker = FixtureTracker(event_store=store)
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_test_flag",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
    )

    prospective_events = store.list_prospective_events()
    assert len(prospective_events) == 0, "Stage 2 test events entered prospective listing"


def test_adversarial_swapped_teams_and_self_play():
    """Adversarial: Swapped home/away teams on same provider ID or self-play must fail closed."""
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    # Self-play rejection
    with pytest.raises(IdentityConflictError, match="Self-play prohibited"):
        tracker.process_provider_event(
            provider_namespace="the_odds_api",
            provider_event_id="evt_self",
            competition_id="E0",
            season_id="2627",
            home_team_id="ARS",
            away_team_id="ARS",
            scheduled_kickoff_utc=k_time,
        )

    # Initial legitimate event
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_swap",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
    )

    # Corrupted feed swaps home and away on same provider ID
    with pytest.raises(IdentityConflictError, match="conflicts with incoming"):
        tracker.process_provider_event(
            provider_namespace="the_odds_api",
            provider_event_id="evt_swap",
            competition_id="E0",
            season_id="2627",
            home_team_id="CHE",
            away_team_id="ARS",
            scheduled_kickoff_utc=k_time,
        )


def test_adversarial_out_of_order_event_rebuild_deterministic():
    """Item 7 & 9: Events delivered in retry order or ingestion order differing from provider timestamp rebuild deterministically."""
    store = ImmutableEventStore()
    tracker = FixtureTracker(event_store=store)

    t0_provider = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    t1_provider = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    k1 = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)
    k2 = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)

    # Event 1: SCHEDULED at t0
    res = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_ooo",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
        reported_state=FixtureLifecycleState.SCHEDULED,
        provider_event_timestamp_utc=t0_provider,
    )

    # Event 2: RESCHEDULED at t1
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_ooo",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        reported_state=FixtureLifecycleState.RESCHEDULED,
        provider_event_timestamp_utc=t1_provider,
    )

    fid = res.internal_fixture_id
    events = tracker.get_events_for_fixture(fid)
    assert len(events) == 2

    # Reverse the raw list (simulating reversed delivery during backfill/retry)
    reversed_events = list(reversed(events))
    reconstructed = rebuild_projection_from_events(reversed_events)

    assert reconstructed.internal_fixture_id == fid
    assert reconstructed.current_state == FixtureLifecycleState.RESCHEDULED
    assert reconstructed.latest_scheduled_kickoff_utc == k2


def test_terminal_state_rejects_stale_pre_terminal_event():
    """Item 9: A fixture in terminal state (OFFICIAL_RESULT_STANDS) rejects subsequent stale pre-terminal events fail-closed."""
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    # Progression to terminal state
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.STARTED,
        actual_kickoff_utc=k_time,
        actual_kickoff_status=ActualKickoffStatus.PROVIDER_REPORTED,
    )
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.COMPLETED,
    )
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.OFFICIAL_RESULT_STANDS,
    )

    # Stale delayed packet arrives reporting match as 'STARTED'
    with pytest.raises(IllegalStateTransitionError, match="terminal absorbing state"):
        tracker.process_provider_event(
            provider_namespace="the_odds_api",
            provider_event_id="evt_term_test",
            competition_id="E0",
            season_id="2627",
            home_team_id="ARS",
            away_team_id="CHE",
            scheduled_kickoff_utc=k_time,
            reported_state=FixtureLifecycleState.STARTED,
        )


def test_delayed_postponement_event_ordering():
    """Item 9: Delayed postponement event rebuilds in canonical temporal order."""
    store = ImmutableEventStore()
    tracker = FixtureTracker(event_store=store)

    t0 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)  # Postponement
    t2 = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)  # Rescheduled
    k1 = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)
    k2 = datetime(2026, 10, 15, 19, 45, tzinfo=timezone.utc)

    # 1. SCHEDULED
    res = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_postpone_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
        reported_state=FixtureLifecycleState.SCHEDULED,
        provider_event_timestamp_utc=t0,
    )
    fid = res.internal_fixture_id

    # 2. POSTPONED
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_postpone_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k1,
        reported_state=FixtureLifecycleState.POSTPONED,
        provider_event_timestamp_utc=t1,
    )

    # 3. RESCHEDULED
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_postpone_test",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k2,
        reported_state=FixtureLifecycleState.RESCHEDULED,
        provider_event_timestamp_utc=t2,
    )

    events = tracker.get_events_for_fixture(fid)
    assert len(events) == 3

    # Permute event order to simulate delayed arrival during replay
    permuted = [events[0], events[2], events[1]]
    rebuilt = rebuild_projection_from_events(permuted)

    assert rebuilt.current_state == FixtureLifecycleState.RESCHEDULED
    assert rebuilt.latest_scheduled_kickoff_utc == k2


def test_stage1_event_store_authoritative_persistence():
    """Item 8: Lifecycle events are persisted strictly through Stage 1 ImmutableEventStore as EventType.FIXTURE_LIFECYCLE."""
    store = ImmutableEventStore()
    tracker = FixtureTracker(event_store=store)
    k_time = datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc)

    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_stage1_binding",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
    )

    # Event exists in the underlying Stage 1 store
    raw_events = list(store._events_by_id.values())
    assert len(raw_events) == 1
    ev = raw_events[0]
    assert ev.event_type.value == "fixture_lifecycle"
    assert ev.test_flag == TestFlag.TEST_ONLY_NON_PROSPECTIVE
    assert ev.payload["competition_id"] == "E0"
    assert "ingestion_timestamp_utc" in ev.payload
    assert "provider_event_timestamp_utc" in ev.payload
    assert ev.payload["fixture_event_sequence"] == 1

