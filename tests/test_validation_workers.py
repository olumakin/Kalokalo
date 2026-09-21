"""
Comprehensive test suite for Stage 3 validation workers:
PredictionDaemon, MarketBenchmarkDaemon, Provenance Engine, and Prospective Activation Guard.
"""
from datetime import datetime, timedelta, timezone
import math

import pandas as pd
import pytest

from src.analytics.devig import devig
from src.config import get_config
from src.engine import DomainPredictionEngine
from src.ingestion.fixture_tracker import (
    ActualKickoffStatus,
    FixtureLifecycleState,
    FixtureTracker,
)
from src.models.dixon_coles import DixonColesModel
from src.tracking.events import (
    EventType,
    ImmutableEventStore,
    TestFlag,
)
from src.validation.manifest import (
    EXTERNAL_CONTRACT_IDENTITIES,
    get_code_identity,
    get_semantic_config_identity,
)
from src.validation.provenance import (
    compute_model_fit_identity,
    compute_training_data_identity,
)
from src.workers import (
    CaptureMode,
    ProspectiveActivationBlockedError,
    ProspectiveActivationContext,
    ProspectiveActivationGuard,
)
from src.workers.market_daemon import (
    MarketBenchmarkDaemon,
    MarketFailureReason,
    join_forecast_and_benchmark,
)
from src.workers.predict_daemon import (
    ModelFailureReason,
    PredictionDaemon,
    TimingContract,
)


def _build_synthetic_training_df() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2024-01-01T12:00:00Z")
    teams = ["ARS", "CHE", "LIV", "MCI"]
    for day, (h, a) in enumerate([(teams[0], teams[1]), (teams[2], teams[3]), (teams[1], teams[2]), (teams[0], teams[3])] * 6):
        match_dt = start + pd.Timedelta(days=day)
        avail_dt = match_dt + pd.Timedelta(hours=2)
        rows.append({
            "date": match_dt.isoformat(),
            "league": "E0",
            "home_team": h,
            "away_team": a,
            "home_goals": 2 if h == "ARS" else 1,
            "away_goals": 1,
            "season": "2324",
            "match_id": f"m_{day}_{h}_{a}",
            "status": "COMPLETED",
            "result_available_at_utc": avail_dt.isoformat(),
            "result_availability_provenance": "PROVIDER_REPORTED",
        })
    return pd.DataFrame(rows)


def _build_synthetic_fitted_models() -> dict[str, DixonColesModel]:
    df = _build_synthetic_training_df()
    df["date"] = pd.to_datetime(df["date"])
    engine = DomainPredictionEngine()
    return engine.fit_leagues(df)


@pytest.fixture
def training_df():
    return _build_synthetic_training_df()


@pytest.fixture
def fitted_models():
    return _build_synthetic_fitted_models()


@pytest.fixture
def registered_fixture():
    tracker = FixtureTracker()
    k_time = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
    res = tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_pred_test_01",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    proj = tracker.get_projection(res.internal_fixture_id)
    return tracker, proj


# ============================================================================
# 1. TRAINING_DATA_IDENTITY TESTS (Mandatory Item 1)
# ============================================================================


def test_training_data_identity_deterministic(training_df):
    """Deterministic: identical training observations produce identical SHA-256 digest."""
    cutoff = "2024-02-01T00:00:00Z"
    h1 = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff, xi=0.0018)
    h2 = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff, xi=0.0018)

    assert len(h1) == 64
    assert h1 == h2


def test_training_row_reordering_identity_stable(training_df):
    """Row reordering: shuffling rows produces identical SHA-256 digest."""
    cutoff = "2024-02-01T00:00:00Z"
    h_orig = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff, xi=0.0018)

    # Shuffle rows radically
    shuffled_df = training_df.sample(frac=1.0, random_state=123).reset_index(drop=True)
    h_shuffled = compute_training_data_identity(shuffled_df, "E0", training_information_cutoff_utc=cutoff, xi=0.0018)

    assert h_orig == h_shuffled


def test_training_data_change_changes_identity(training_df):
    """Material change: altering one score, team, date, or parameter changes digest."""
    cutoff = "2024-02-01T00:00:00Z"
    h_base = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff, xi=0.0018)

    # 1. Alter one scoreline
    df_score = training_df.copy()
    df_score.loc[0, "home_goals"] = int(df_score.loc[0, "home_goals"]) + 1
    h_score = compute_training_data_identity(df_score, "E0", training_information_cutoff_utc=cutoff, xi=0.0018)
    assert h_score != h_base

    # 2. Alter fitting parameter xi
    h_xi = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff, xi=0.0025)
    assert h_xi != h_base


# ============================================================================
# 2. MODEL_FIT_IDENTITY TESTS (Mandatory Item 2)
# ============================================================================


def test_prediction_bound_to_training_data_identity(fitted_models, registered_fixture, training_df):
    """Successful prediction must bind to training_data_identity and model_fit_identity."""
    tracker, fixture = registered_fixture
    store = ImmutableEventStore()
    daemon = PredictionDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)

    cutoff = "2024-02-01T00:00:00Z"
    td_id = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff, xi=0.0018)

    t_pred = fixture.latest_scheduled_kickoff_utc - timedelta(hours=2)
    ev = daemon.predict_and_capture(
        fixture,
        fitted_models,
        as_of_utc=t_pred,
        training_data_identities={"E0": td_id},
        training_information_cutoffs_utc={"E0": cutoff},
    )

    assert ev.event_type == EventType.PREDICTION_CAPTURE_SUCCESS
    assert ev.payload["capture_status"] == "PREDICTION_CAPTURE_SUCCESS"
    assert ev.payload["training_data_identity"] == td_id
    assert ev.payload["model_fit_identity"] is not None
    assert len(ev.payload["model_fit_identity"]) == 64
    assert ev.payload["forecast_evaluable"] is True


def test_training_data_change_changes_fit_identity(fitted_models, training_df):
    """Altering training data changes model_fit_identity."""
    model = fitted_models["E0"]
    code_sha = get_code_identity()
    config_sha = get_semantic_config_identity()

    cutoff = "2024-02-01T00:00:00Z"
    td_id1 = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff)

    df_mut = training_df.copy()
    df_mut.loc[0, "away_goals"] = int(df_mut.loc[0, "away_goals"]) + 2
    td_id2 = compute_training_data_identity(df_mut, "E0", training_information_cutoff_utc=cutoff)

    fid1 = compute_model_fit_identity(model, "E0", td_id1, config_sha, code_sha)
    fid2 = compute_model_fit_identity(model, "E0", td_id2, config_sha, code_sha)

    assert fid1 != fid2


def test_model_fit_identity_changes_on_semantic_change(fitted_models, training_df):
    """Altering semantic configuration hash changes model_fit_identity."""
    model = fitted_models["E0"]
    code_sha = get_code_identity()
    config_sha1 = get_semantic_config_identity()
    config_sha2 = "0" * 64

    cutoff = "2024-02-01T00:00:00Z"
    td_id = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff)

    fid1 = compute_model_fit_identity(model, "E0", td_id, config_sha1, code_sha)
    fid2 = compute_model_fit_identity(model, "E0", td_id, config_sha2, code_sha)

    assert fid1 != fid2


def test_result_availability_provenance_bound_to_training_identity(training_df):
    """Result availability provenance is bound to training data identity and affects hash."""
    cutoff = "2024-02-01T00:00:00Z"
    h_provider = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff)

    # Change provenance to OFFICIALLY_VERIFIED
    df_verified = training_df.copy()
    df_verified["result_availability_provenance"] = "OFFICIALLY_VERIFIED"
    h_verified = compute_training_data_identity(df_verified, "E0", training_information_cutoff_utc=cutoff)

    assert h_provider != h_verified


def test_result_availability_change_can_change_training_identity(training_df, fitted_models):
    """Changing result availability timestamp changes TRAINING_DATA_IDENTITY and MODEL_FIT_IDENTITY."""
    cutoff = "2024-02-01T00:00:00Z"
    h1 = compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff)

    # Shift result availability time by 1 hour on match 0
    df_shifted = training_df.copy()
    orig_avail = pd.to_datetime(df_shifted.loc[0, "result_available_at_utc"])
    df_shifted.loc[0, "result_available_at_utc"] = (orig_avail + pd.Timedelta(hours=1)).isoformat()
    h2 = compute_training_data_identity(df_shifted, "E0", training_information_cutoff_utc=cutoff)

    assert h1 != h2

    # Verify MODEL_FIT_IDENTITY also changes
    model = fitted_models["E0"]
    code_sha = get_code_identity()
    cfg_sha = get_semantic_config_identity()
    fid1 = compute_model_fit_identity(model, "E0", h1, cfg_sha, code_sha)
    fid2 = compute_model_fit_identity(model, "E0", h2, cfg_sha, code_sha)

    assert fid1 != fid2


# ============================================================================
# 3. INFORMATION_CUTOFF & TEMPORAL LEAKAGE TESTS (Mandatory Item 3 & 5 & 6)
# ============================================================================


def test_same_calendar_date_future_result_excluded():
    """Scenario: Prediction at 10:00 UTC, cutoff at 09:30 UTC.
    Candidate match on same calendar date has result available at 15:00 UTC.
    Must be strictly excluded from training data identity (fails closed).
    """
    df = pd.DataFrame([{
        "date": "2026-09-26T08:00:00Z",
        "league": "E0",
        "home_team": "ARS",
        "away_team": "CHE",
        "home_goals": 2,
        "away_goals": 1,
        "season": "2627",
        "match_id": "m_same_day",
        "status": "COMPLETED",
        "result_available_at_utc": "2026-09-26T15:00:00Z",  # 15:00 UTC (after 09:30 cutoff)
        "result_availability_provenance": "PROVIDER_REPORTED",
    }])
    cutoff = "2026-09-26T09:30:00Z"

    with pytest.raises(ValueError, match="Temporal look-ahead violation"):
        compute_training_data_identity(df, "E0", training_information_cutoff_utc=cutoff)


def test_result_available_before_cutoff_included():
    """Scenario: Result available at 09:00 UTC, cutoff at 09:30 UTC, prediction at 10:00 UTC.
    Row is eligible and successfully incorporated into training identity.
    """
    df = pd.DataFrame([{
        "date": "2026-09-26T07:00:00Z",
        "league": "E0",
        "home_team": "ARS",
        "away_team": "CHE",
        "home_goals": 2,
        "away_goals": 1,
        "season": "2627",
        "match_id": "m_prior",
        "status": "COMPLETED",
        "result_available_at_utc": "2026-09-26T09:00:00Z",  # 09:00 UTC (before 09:30 cutoff)
        "result_availability_provenance": "PROVIDER_REPORTED",
    }])
    cutoff = "2026-09-26T09:30:00Z"
    h = compute_training_data_identity(df, "E0", training_information_cutoff_utc=cutoff)
    assert len(h) == 64


def test_unknown_result_availability_fails_closed():
    """Rows with missing or UNKNOWN result availability must fail closed for prospective fitting."""
    # Subcase A: Missing result_available_at_utc
    df_missing = pd.DataFrame([{
        "date": "2026-09-25T15:00:00Z",
        "league": "E0",
        "home_team": "ARS",
        "away_team": "CHE",
        "home_goals": 2,
        "away_goals": 1,
        "status": "COMPLETED",
        "result_availability_provenance": "PROVIDER_REPORTED",
    }])
    with pytest.raises(ValueError, match="Unknown result availability fails closed"):
        compute_training_data_identity(df_missing, "E0", training_information_cutoff_utc="2026-09-26T00:00:00Z")

    # Subcase B: Provenance is explicitly UNKNOWN
    df_unknown = pd.DataFrame([{
        "date": "2026-09-25T15:00:00Z",
        "league": "E0",
        "home_team": "ARS",
        "away_team": "CHE",
        "home_goals": 2,
        "away_goals": 1,
        "status": "COMPLETED",
        "result_available_at_utc": "2026-09-25T17:00:00Z",
        "result_availability_provenance": "UNKNOWN",
    }])
    with pytest.raises(ValueError, match="Unknown result availability fails closed"):
        compute_training_data_identity(df_unknown, "E0", training_information_cutoff_utc="2026-09-26T00:00:00Z")


def test_future_result_cannot_enter_prediction_fit(training_df, fitted_models, registered_fixture):
    """Hard anti-look-ahead invariant: result available after cutoff cannot enter fit, and cutoff must precede prediction time."""
    cutoff = "2024-01-10T00:00:00Z"

    # Part A: Dataset with result available after training cutoff is rejected
    with pytest.raises(ValueError, match="Temporal look-ahead violation"):
        compute_training_data_identity(training_df, "E0", training_information_cutoff_utc=cutoff)

    # Part B: Prediction with training information cutoff >= prediction time is rejected
    tracker, fixture = registered_fixture
    daemon = PredictionDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    t_pred = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    future_cutoff = datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc)  # After t_pred

    with pytest.raises(ValueError, match="Anti-look-ahead invariant violated"):
        daemon.predict_and_capture(
            fixture,
            fitted_models,
            as_of_utc=t_pred,
            training_information_cutoffs_utc={"E0": future_cutoff},
        )


def test_unresolved_abandoned_result_not_training_eligible():
    """Unresolved matches (e.g. ABANDONED_PENDING_RULING) cannot become training observations even if partial score exists."""
    df_abandoned = pd.DataFrame([{
        "date": "2026-09-20T15:00:00Z",
        "league": "E0",
        "home_team": "ARS",
        "away_team": "CHE",
        "home_goals": 1,
        "away_goals": 0,
        "season": "2627",
        "match_id": "m_abandoned",
        "status": "ABANDONED_PENDING_RULING",
        "result_available_at_utc": "2026-09-20T17:00:00Z",
        "result_availability_provenance": "PROVIDER_REPORTED",
    }])
    with pytest.raises(ValueError, match="Unresolved or non-final match state"):
        compute_training_data_identity(df_abandoned, "E0", training_information_cutoff_utc="2026-09-25T00:00:00Z")


# ============================================================================
# 4. FIXTURE LIFECYCLE GATING TESTS (Mandatory Item 4)
# ============================================================================


def test_postponed_fixture_not_forecast_captured(fitted_models, registered_fixture):
    """POSTPONED fixture must not generate normal forecast capture."""
    tracker, fixture = registered_fixture
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_pred_test_01",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=fixture.latest_scheduled_kickoff_utc,
        reported_state=FixtureLifecycleState.POSTPONED,
    )
    postponed_proj = tracker.get_projection(fixture.internal_fixture_id)

    daemon = PredictionDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    ev = daemon.predict_and_capture(
        postponed_proj,
        fitted_models,
        as_of_utc=fixture.latest_scheduled_kickoff_utc - timedelta(hours=3),
    )

    assert ev.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev.payload["failure_reason"] == ModelFailureReason.LIFECYCLE_POSTPONED.value
    assert ev.payload["forecast_evaluable"] is False
    assert ev.payload["model_probabilities"] is None


def test_terminal_fixture_not_forecast_captured(fitted_models):
    """Terminal lifecycle states (COMPLETED, OFFICIAL_RESULT_STANDS, VOIDED, ABANDONED) block forecast capture."""
    daemon = PredictionDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    k_time = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)

    # 1. VOIDED directly from SCHEDULED
    t1 = FixtureTracker()
    res1 = t1.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_01",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    t1.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_01",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.VOIDED,
    )
    proj1 = t1.get_projection(res1.internal_fixture_id)
    ev1 = daemon.predict_and_capture(proj1, fitted_models, as_of_utc=k_time - timedelta(hours=3))
    assert ev1.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev1.payload["failure_reason"] == ModelFailureReason.LIFECYCLE_TERMINAL.value
    assert ev1.payload["forecast_evaluable"] is False
    assert ev1.payload["model_probabilities"] is None

    # 2. STARTED -> COMPLETED
    t2 = FixtureTracker()
    res2 = t2.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_02",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    t2.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_02",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.STARTED,
    )
    t2.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_02",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.COMPLETED,
    )
    proj2 = t2.get_projection(res2.internal_fixture_id)
    ev2 = daemon.predict_and_capture(proj2, fitted_models, as_of_utc=k_time - timedelta(hours=3))
    assert ev2.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev2.payload["failure_reason"] == ModelFailureReason.LIFECYCLE_TERMINAL.value

    # 3. STARTED -> COMPLETED -> OFFICIAL_RESULT_STANDS
    t2.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_02",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.OFFICIAL_RESULT_STANDS,
    )
    proj3 = t2.get_projection(res2.internal_fixture_id)
    ev3 = daemon.predict_and_capture(proj3, fitted_models, as_of_utc=k_time - timedelta(hours=3))
    assert ev3.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev3.payload["failure_reason"] == ModelFailureReason.LIFECYCLE_TERMINAL.value

    # 4. STARTED -> ABANDONED_PENDING_RULING
    t4 = FixtureTracker()
    res4 = t4.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_04",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.SCHEDULED,
    )
    t4.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_04",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.STARTED,
    )
    t4.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_term_04",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_time,
        reported_state=FixtureLifecycleState.ABANDONED_PENDING_RULING,
    )
    proj4 = t4.get_projection(res4.internal_fixture_id)
    ev4 = daemon.predict_and_capture(proj4, fitted_models, as_of_utc=k_time - timedelta(hours=3))
    assert ev4.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev4.payload["failure_reason"] == ModelFailureReason.LIFECYCLE_TERMINAL.value


def test_rescheduled_fixture_uses_current_schedule(fitted_models, registered_fixture):
    """RESCHEDULED fixture evaluates timing against current authoritative schedule."""
    tracker, fixture = registered_fixture
    daemon = PredictionDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN, decision_lead_time_seconds=3600)

    # Reschedule to 1 month later
    k_new = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_pred_test_01",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=k_new,
        reported_state=FixtureLifecycleState.RESCHEDULED,
    )
    rescheduled_proj = tracker.get_projection(fixture.internal_fixture_id)

    # 1. Prediction 2 hours before new schedule -> succeeds
    t_valid = k_new - timedelta(hours=2)
    ev_valid = daemon.predict_and_capture(rescheduled_proj, fitted_models, as_of_utc=t_valid)
    assert ev_valid.event_type == EventType.PREDICTION_CAPTURE_SUCCESS
    assert ev_valid.payload["eligibility_state"] == "ELIGIBLE"
    assert ev_valid.payload["scheduled_kickoff_utc"] == k_new.isoformat()

    # 2. Prediction 30 minutes before new schedule -> rejected as post-cutoff
    t_invalid = k_new - timedelta(minutes=30)
    ev_invalid = daemon.predict_and_capture(rescheduled_proj, fitted_models, as_of_utc=t_invalid)
    assert ev_invalid.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev_invalid.payload["failure_reason"] == ModelFailureReason.POST_CUTOFF_ATTEMPT.value


def test_started_fixture_not_forecast_captured(fitted_models, registered_fixture):
    """STARTED fixture must not permit pre-match forecast capture."""
    tracker, fixture = registered_fixture
    tracker.process_provider_event(
        provider_namespace="the_odds_api",
        provider_event_id="evt_pred_test_01",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        scheduled_kickoff_utc=fixture.latest_scheduled_kickoff_utc,
        reported_state=FixtureLifecycleState.STARTED,
    )
    started_proj = tracker.get_projection(fixture.internal_fixture_id)

    daemon = PredictionDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    ev = daemon.predict_and_capture(
        started_proj,
        fitted_models,
        as_of_utc=fixture.latest_scheduled_kickoff_utc - timedelta(hours=3),
    )

    assert ev.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev.payload["failure_reason"] == ModelFailureReason.LIFECYCLE_STARTED.value
    assert ev.payload["forecast_evaluable"] is False
    assert ev.payload["model_probabilities"] is None


# ============================================================================
# 5. SUCCESS VS FAILURE SEPARATION TESTS (Mandatory Item 5)
# ============================================================================


def test_capture_failure_not_forecast_evaluable(fitted_models, registered_fixture):
    """Failure events must be structurally distinct from success events and not forecast evaluable."""
    tracker, fixture = registered_fixture
    store = ImmutableEventStore()
    daemon = PredictionDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)

    # 1. Post cutoff attempt
    t_after = fixture.latest_scheduled_kickoff_utc - timedelta(minutes=10)
    ev_cutoff = daemon.predict_and_capture(fixture, fitted_models, as_of_utc=t_after)
    assert ev_cutoff.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev_cutoff.payload["capture_status"] == "PREDICTION_CAPTURE_FAILURE"
    assert ev_cutoff.payload["failure_reason"] == ModelFailureReason.POST_CUTOFF_ATTEMPT.value
    assert ev_cutoff.payload["forecast_evaluable"] is False
    assert ev_cutoff.payload["model_probabilities"] is None

    # 2. Unknown team attempt
    t_ok = fixture.latest_scheduled_kickoff_utc - timedelta(hours=2)
    bad_fixture = fixture.model_copy(update={"away_team_id": "UNKNOWN_FC"})
    ev_unknown = daemon.predict_and_capture(bad_fixture, fitted_models, as_of_utc=t_ok)
    assert ev_unknown.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev_unknown.payload["failure_reason"] == ModelFailureReason.UNKNOWN_TEAM.value
    assert ev_unknown.payload["forecast_evaluable"] is False
    assert ev_unknown.payload["model_probabilities"] is None

    # 3. Model non-convergence attempt
    non_conv_model = DixonColesModel()
    non_conv_model.converged_ = False
    ev_nonconv = daemon.predict_and_capture(fixture, {"E0": non_conv_model}, as_of_utc=t_ok)
    assert ev_nonconv.event_type == EventType.PREDICTION_CAPTURE_FAILURE
    assert ev_nonconv.payload["failure_reason"] == ModelFailureReason.MODEL_NON_CONVERGENCE.value
    assert ev_nonconv.payload["forecast_evaluable"] is False
    assert ev_nonconv.payload["model_probabilities"] is None

    # Verify denominator remains 0
    assert daemon.get_prospective_denominator() == 0


# ============================================================================
# 6. HARDEN PROSPECTIVE ACTIVATION TESTS (Mandatory Item 6)
# ============================================================================


def test_prospective_rejected_without_epoch_sha():
    """Prospective activation rejected if EPOCH_MODEL_SHA is None or UNASSIGNED."""
    ctx_none = ProspectiveActivationContext(epoch_state="ACTIVE", epoch_model_sha=None)
    with pytest.raises(ProspectiveActivationBlockedError, match="EPOCH_MODEL_SHA"):
        ProspectiveActivationGuard.verify(ctx_none)

    ctx_unassigned = ProspectiveActivationContext(epoch_state="ACTIVE", epoch_model_sha="UNASSIGNED")
    with pytest.raises(ProspectiveActivationBlockedError, match="EPOCH_MODEL_SHA"):
        ProspectiveActivationGuard.verify(ctx_unassigned)


def test_prospective_rejected_on_head_sha_mismatch():
    """Prospective activation rejected if runtime HEAD does not match EPOCH_MODEL_SHA."""
    fake_sha = "a" * 40
    ctx = ProspectiveActivationContext(epoch_state="ACTIVE", epoch_model_sha=fake_sha)
    with pytest.raises(ProspectiveActivationBlockedError, match="RUNTIME_HEAD_SHA"):
        ProspectiveActivationGuard.verify(ctx)


def test_prospective_rejected_on_dirty_tree(monkeypatch):
    """Prospective activation rejected if working tree is dirty."""
    monkeypatch.setattr("src.workers.activation_guard.is_working_tree_dirty", lambda **kwargs: True)
    head_sha = get_code_identity()
    ctx = ProspectiveActivationContext(
        epoch_state="ACTIVE",
        epoch_model_sha=head_sha,
        allow_dirty=False,
        training_data_provenance={"E0": "a" * 64},
    )
    with pytest.raises(ProspectiveActivationBlockedError, match="WORKING_TREE is dirty"):
        ProspectiveActivationGuard.verify(ctx)


def test_prospective_rejected_on_semantic_config_drift():
    """Prospective activation rejected if runtime semantic config hash does not match expected."""
    head_sha = get_code_identity()
    ctx = ProspectiveActivationContext(
        epoch_state="ACTIVE",
        epoch_model_sha=head_sha,
        allow_dirty=True,
        expected_semantic_config_identity="0" * 64,
    )
    with pytest.raises(ProspectiveActivationBlockedError, match="SEMANTIC_CONFIG_IDENTITY drift"):
        ProspectiveActivationGuard.verify(ctx)


def test_prospective_rejected_on_contract_drift():
    """Prospective activation rejected if external contract identities drift."""
    head_sha = get_code_identity()
    cfg_sha = get_semantic_config_identity()
    ctx = ProspectiveActivationContext(
        epoch_state="ACTIVE",
        epoch_model_sha=head_sha,
        allow_dirty=True,
        expected_semantic_config_identity=cfg_sha,
        expected_contracts={"forecast_capture_contract": "ForecastCaptureContract::v99.0"},
    )
    with pytest.raises(ProspectiveActivationBlockedError, match="Contract drift"):
        ProspectiveActivationGuard.verify(ctx)


def test_prospective_rejected_without_training_provenance():
    """Prospective activation rejected if training data provenance is missing or empty."""
    head_sha = get_code_identity()
    cfg_sha = get_semantic_config_identity()
    ctx = ProspectiveActivationContext(
        epoch_state="ACTIVE",
        epoch_model_sha=head_sha,
        allow_dirty=True,
        expected_semantic_config_identity=cfg_sha,
        training_data_provenance=None,
    )
    with pytest.raises(ProspectiveActivationBlockedError, match="TRAINING_DATA_PROVENANCE"):
        ProspectiveActivationGuard.verify(ctx)


# ============================================================================
# 7. MARKET TIMESTAMP PROVENANCE TESTS (Mandatory Item 7)
# ============================================================================


def test_provider_quote_timestamp_present_preserved(registered_fixture):
    """When provider quote timestamp is present, it is preserved with status PRESENT."""
    tracker, fixture = registered_fixture
    m_daemon = MarketBenchmarkDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    t_quote = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    t_ingest = datetime(2026, 9, 26, 12, 0, 5, tzinfo=timezone.utc)

    ev = m_daemon.capture_benchmark_quote(
        internal_fixture_id=fixture.internal_fixture_id,
        source="the_odds_api",
        odds_home=2.10,
        odds_draw=3.40,
        odds_away=3.60,
        provider_quote_timestamp_utc=t_quote,
        as_of_utc=t_ingest,
    )

    assert ev.payload["provider_quote_timestamp_status"] == "PRESENT"
    assert ev.payload["provider_quote_timestamp_utc"] == t_quote.isoformat()
    assert ev.payload["ingestion_timestamp_utc"] == t_ingest.isoformat()
    assert ev.payload["freshness_evaluable"] is True


def test_provider_quote_timestamp_unknown_preserved(registered_fixture):
    """When provider quote timestamp is missing, status is UNKNOWN and timestamp is None."""
    tracker, fixture = registered_fixture
    m_daemon = MarketBenchmarkDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    t_ingest = datetime(2026, 9, 26, 12, 0, 5, tzinfo=timezone.utc)

    ev = m_daemon.capture_benchmark_quote(
        internal_fixture_id=fixture.internal_fixture_id,
        source="the_odds_api",
        odds_home=2.10,
        odds_draw=3.40,
        odds_away=3.60,
        provider_quote_timestamp_utc=None,
        as_of_utc=t_ingest,
    )

    assert ev.payload["provider_quote_timestamp_status"] == "UNKNOWN"
    assert ev.payload["provider_quote_timestamp_utc"] is None
    assert ev.payload["ingestion_timestamp_utc"] == t_ingest.isoformat()
    assert ev.payload["freshness_evaluable"] is False


def test_ingestion_time_not_substituted_for_quote_time(registered_fixture):
    """Ingestion time must never be substituted for missing provider quote time."""
    tracker, fixture = registered_fixture
    m_daemon = MarketBenchmarkDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    t_ingest = datetime(2026, 9, 26, 12, 0, 5, tzinfo=timezone.utc)

    ev = m_daemon.capture_benchmark_quote(
        internal_fixture_id=fixture.internal_fixture_id,
        source="the_odds_api",
        odds_home=2.10,
        odds_draw=3.40,
        odds_away=3.60,
        provider_quote_timestamp_utc=None,
        as_of_utc=t_ingest,
    )

    assert ev.payload["provider_quote_timestamp_utc"] is None
    assert ev.payload["ingestion_timestamp_utc"] == t_ingest.isoformat()
    assert ev.payload["provider_quote_timestamp_utc"] != ev.payload["ingestion_timestamp_utc"]


def test_future_provider_quote_timestamp_rejected(registered_fixture):
    """Quote timestamp materially in the future relative to ingestion timestamp (>120s) is rejected."""
    tracker, fixture = registered_fixture
    m_daemon = MarketBenchmarkDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    t_ingest = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    t_future = t_ingest + timedelta(minutes=5)

    with pytest.raises(ValueError, match="Clock anomaly detected"):
        m_daemon.capture_benchmark_quote(
            internal_fixture_id=fixture.internal_fixture_id,
            source="the_odds_api",
            odds_home=2.10,
            odds_draw=3.40,
            odds_away=3.60,
            provider_quote_timestamp_utc=t_future,
            as_of_utc=t_ingest,
        )


# ============================================================================
# 8. RAW MARKET RECONSTRUCTION TESTS (Mandatory Item 8)
# ============================================================================


def test_market_devig_reconstructable_from_raw_snapshot(registered_fixture):
    """Stored market snapshot contains all evidence required to reconstruct derived probabilities."""
    tracker, fixture = registered_fixture
    m_daemon = MarketBenchmarkDaemon(capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN, devig_method="shin")
    t_now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    raw_h = 2.15
    raw_d = 3.35
    raw_a = 3.50

    ev = m_daemon.capture_benchmark_quote(
        internal_fixture_id=fixture.internal_fixture_id,
        source="pinnacle",
        odds_home=raw_h,
        odds_draw=raw_d,
        odds_away=raw_a,
        provider_quote_timestamp_utc=t_now,
        as_of_utc=t_now,
    )

    payload = ev.payload
    assert payload["raw_odds_home"] == raw_h
    assert payload["raw_odds_draw"] == raw_d
    assert payload["raw_odds_away"] == raw_a
    assert payload["devig_method"] == "shin"

    # Reconstruct independently from stored payload
    recomputed_h, recomputed_d, recomputed_a = devig(
        payload["raw_odds_home"],
        payload["raw_odds_draw"],
        payload["raw_odds_away"],
        method=payload["devig_method"],
    )

    assert recomputed_h == pytest.approx(payload["derived_market_p_home"], abs=1e-6)
    assert recomputed_d == pytest.approx(payload["derived_market_p_draw"], abs=1e-6)
    assert recomputed_a == pytest.approx(payload["derived_market_p_away"], abs=1e-6)


# ============================================================================
# 9. EXTERNAL CONTRACT IDENTITIES EMBEDDED TESTS (Mandatory Item 9)
# ============================================================================


def test_external_contracts_embedded_in_stored_evidence(fitted_models, registered_fixture):
    """Forecast and market events carry ForecastCaptureContract, MarketBenchmarkContract, and ForecastTimingContract."""
    tracker, fixture = registered_fixture
    store = ImmutableEventStore()
    p_daemon = PredictionDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    m_daemon = MarketBenchmarkDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)

    t_now = fixture.latest_scheduled_kickoff_utc - timedelta(hours=2)
    p_ev = p_daemon.predict_and_capture(fixture, fitted_models, as_of_utc=t_now)
    m_ev = m_daemon.capture_benchmark_quote(
        internal_fixture_id=fixture.internal_fixture_id,
        source="the_odds_api",
        odds_home=2.10,
        odds_draw=3.40,
        odds_away=3.60,
        provider_quote_timestamp_utc=t_now,
        as_of_utc=t_now,
    )

    # Prediction event contracts
    assert p_ev.payload["external_contract_identity"] == "ForecastCaptureContract::v1.0"
    assert p_ev.payload["timing_contract_version"] == "ForecastTimingContract::v1.0"
    assert p_ev.payload["fixture_lifecycle_contract"] == "FixtureLifecycleContract::v1.0"

    # Market event contract
    assert m_ev.payload["external_contract_identity"] == "MarketBenchmarkContract::v1.0"

    # Registry contains them
    assert EXTERNAL_CONTRACT_IDENTITIES["forecast_capture_contract"] == "ForecastCaptureContract::v1.0"
    assert EXTERNAL_CONTRACT_IDENTITIES["market_benchmark_contract"] == "MarketBenchmarkContract::v1.0"
    assert EXTERNAL_CONTRACT_IDENTITIES["forecast_timing_contract"] == "ForecastTimingContract::v1.0"


# ============================================================================
# 10. GENERAL SUITE INTEGRATION TESTS
# ============================================================================


def test_market_outage_does_not_destroy_forecast_capture(fitted_models, registered_fixture):
    """Decoupled: Prediction persists independently when market feed is completely unavailable."""
    tracker, fixture = registered_fixture
    store = ImmutableEventStore()
    p_daemon = PredictionDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    m_daemon = MarketBenchmarkDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)

    t_pred = fixture.latest_scheduled_kickoff_utc - timedelta(hours=2)
    pred_ev = p_daemon.predict_and_capture(fixture, fitted_models, as_of_utc=t_pred)
    assert pred_ev.payload["eligibility_state"] == "ELIGIBLE"
    assert pred_ev.event_type == EventType.PREDICTION_CAPTURE_SUCCESS

    market_ev = m_daemon.capture_benchmark_quote(
        internal_fixture_id=fixture.internal_fixture_id,
        source="the_odds_api",
        odds_home=None,
        odds_draw=None,
        odds_away=None,
        as_of_utc=t_pred,
    )
    assert market_ev.payload["failure_reason"] == MarketFailureReason.MARKET_SOURCE_UNAVAILABLE.value

    retrieved_pred = store.get_by_id(pred_ev.event_id)
    assert retrieved_pred is not None
    assert retrieved_pred.payload["eligibility_state"] == "ELIGIBLE"
    assert retrieved_pred.payload["model_probabilities"] is not None


def test_join_forecast_and_benchmark(fitted_models, registered_fixture):
    """Join forecast prediction and market benchmarks exclusively on internal_fixture_id."""
    tracker, fixture = registered_fixture
    store = ImmutableEventStore()
    p_daemon = PredictionDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    m_daemon = MarketBenchmarkDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)

    t_pred = fixture.latest_scheduled_kickoff_utc - timedelta(hours=2)
    p_daemon.predict_and_capture(fixture, fitted_models, as_of_utc=t_pred)
    m_daemon.capture_benchmark_quote(
        internal_fixture_id=fixture.internal_fixture_id,
        source="the_odds_api",
        odds_home=2.10,
        odds_draw=3.40,
        odds_away=3.60,
        provider_quote_timestamp_utc=t_pred,
        as_of_utc=t_pred,
    )

    joined = join_forecast_and_benchmark(store, fixture.internal_fixture_id)
    assert joined["internal_fixture_id"] == fixture.internal_fixture_id
    assert len(joined["predictions"]) == 1
    assert len(joined["benchmarks"]) == 1


def test_stage3_denominator_remains_zero(fitted_models, registered_fixture):
    """The prospective evaluation denominator must remain strictly zero in Stage 3."""
    tracker, fixture = registered_fixture
    store = ImmutableEventStore()
    p_daemon = PredictionDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)
    t_now = fixture.latest_scheduled_kickoff_utc - timedelta(hours=2)

    p_daemon.predict_and_capture(fixture, fitted_models, as_of_utc=t_now)
    assert p_daemon.get_prospective_denominator() == 0
    assert len(store.list_prospective_events()) == 0


def test_a09_boundary_preserved(registered_fixture):
    """Strict A09 boundary: consensus market benchmark quotes must NEVER be marked as executable."""
    tracker, fixture = registered_fixture
    store = ImmutableEventStore()
    m_daemon = MarketBenchmarkDaemon(event_store=store, capture_mode=CaptureMode.PRE_EPOCH_DRY_RUN)

    ev = m_daemon.capture_benchmark_quote(
        internal_fixture_id=fixture.internal_fixture_id,
        source="the_odds_api",
        odds_home=2.10,
        odds_draw=3.40,
        odds_away=3.60,
    )

    assert ev.payload["is_executable_price"] is False
    assert "bet_size" not in ev.payload
    assert "kelly_fraction" not in ev.payload
    assert "expected_value" not in ev.payload
