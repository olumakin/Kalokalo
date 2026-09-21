"""
Comprehensive test suite for Prospective Paper Decision Worker (Stage 4 / A09).

Verifies PaperDecisionContract::v1.0 invariants:
- Registration in EXTERNAL_CONTRACT_IDENTITIES
- Economic separation of benchmark quote (information filter) and execution quote (EV & Kelly)
- Strict prohibition of benchmark odds fallback (fail-closed on absent/invalid execution quote)
- Prediction timing cutoff enforcement (t_decision < t_kickoff_sched - lead_time)
- Fixture lifecycle eligibility gating (SCHEDULED/RESCHEDULED only)
- Causal ordering validation (quote/prediction ts <= decision ts)
- Fractional Kelly position sizing with c = 0.15 multiplier
- Dual-layer exposure capping: single_match_cap = 0.025 and daily_slate_cap = 0.08
- Deterministic exposure state snapshotting and replay
- Prospective activation gating and non-prospective tagging
"""
from datetime import datetime, timedelta, timezone
import pytest

from src.analytics.edge import calculate_ev, kelly_fraction
from src.ingestion.execution_feed import ExecutionQuoteDaemon
from src.ingestion.fixture_tracker import (
    ActualKickoffStatus,
    FixtureLifecycleState,
    FixtureStateProjection,
)
from src.tracking.events import (
    EventType,
    ImmutableEventStore,
    ProspectiveEvent,
    TestFlag,
)
from src.validation.manifest import EXTERNAL_CONTRACT_IDENTITIES
from src.workers import (
    CaptureMode,
    ProspectiveActivationBlockedError,
)
from src.workers.decision_daemon import (
    PAPER_DECISION_CONTRACT_VERSION,
    DecisionDaemon,
    DecisionFailureReason,
)
from src.workers.market_daemon import MarketBenchmarkDaemon
from src.workers.predict_daemon import TimingContract


def _create_synthetic_fixture_projection(
    scheduled_kickoff: datetime,
    state: FixtureLifecycleState = FixtureLifecycleState.SCHEDULED,
) -> FixtureStateProjection:
    return FixtureStateProjection(
        internal_fixture_id="fix_E0_synth_01",
        competition_id="E0",
        season_id="2627",
        home_team_id="ARS",
        away_team_id="CHE",
        home_display_name="Arsenal",
        away_display_name="Chelsea",
        current_state=state,
        original_scheduled_kickoff_utc=scheduled_kickoff,
        latest_scheduled_kickoff_utc=scheduled_kickoff,
        actual_kickoff_status=ActualKickoffStatus.UNKNOWN,
        actual_kickoff_utc=None,
    )


def _create_synthetic_prediction_event(
    internal_fixture_id: str,
    p_draw: float = 0.32,
    prediction_time: datetime | None = None,
) -> ProspectiveEvent:
    now = prediction_time or datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    return ProspectiveEvent(
        event_id="evt_pred_synth_1",
        event_type=EventType.PREDICTION_CAPTURE_SUCCESS,
        epoch_id="PRE_EPOCH_STAGE_3",
        idempotency_key="a" * 64,
        created_at_utc=now,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={
            "internal_fixture_id": internal_fixture_id,
            "prediction_timestamp_utc": now.isoformat(),
            "model_fit_identity": "b" * 64,
            "training_data_identity": "c" * 64,
            "model_probabilities": {
                "p_home": 0.45,
                "p_draw": p_draw,
                "p_away": 0.23,
            },
        },
    )


@pytest.fixture
def decision_env():
    store = ImmutableEventStore()
    timing = TimingContract(decision_lead_time_seconds=3600)  # 60 min
    daemon = DecisionDaemon(
        event_store=store,
        timing_contract=timing,
        min_ev=0.03,
        kelly_multiplier=0.15,
        single_match_cap=0.025,
        daily_slate_cap=0.08,
    )
    sched_kickoff = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
    cutoff = sched_kickoff - timedelta(hours=1)
    decision_time = cutoff - timedelta(minutes=15)  # 13:45 UTC (valid, before 14:00 cutoff)
    quote_time = decision_time - timedelta(minutes=5)

    proj = _create_synthetic_fixture_projection(sched_kickoff)
    pred_event = _create_synthetic_prediction_event("fix_E0_synth_01", p_draw=0.32, prediction_time=quote_time)

    # Benchmark quote daemon
    bm_daemon = MarketBenchmarkDaemon(event_store=store)
    bm_event = bm_daemon.capture_benchmark_quote(
        internal_fixture_id="fix_E0_synth_01",
        source="the_odds_api_consensus",
        odds_home=2.10,
        odds_draw=3.40,  # devigged draw ~ 0.28
        odds_away=3.60,
        provider_quote_timestamp_utc=quote_time,
        as_of_utc=decision_time,
    )

    # Execution quote daemon
    exec_daemon = ExecutionQuoteDaemon(event_store=store)
    exec_event = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_synth_01",
        bookmaker="Bet365",
        odds_draw=3.60,
        provider_quote_timestamp_utc=quote_time,
        as_of_utc=decision_time,
    )

    return {
        "store": store,
        "daemon": daemon,
        "proj": proj,
        "pred_event": pred_event,
        "bm_event": bm_event,
        "exec_event": exec_event,
        "decision_time": decision_time,
        "sched_kickoff": sched_kickoff,
    }


def test_paper_decision_contract_registered():
    """PaperDecisionContract::v1.0 must be registered in EXTERNAL_CONTRACT_IDENTITIES."""
    assert "paper_decision_contract" in EXTERNAL_CONTRACT_IDENTITIES
    assert EXTERNAL_CONTRACT_IDENTITIES["paper_decision_contract"] == PAPER_DECISION_CONTRACT_VERSION


def test_qualified_paper_decision_evaluation(decision_env):
    """When information filter and executable EV pass, decision is qualified with Kelly sizing."""
    daemon = decision_env["daemon"]
    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=decision_env["exec_event"],
        as_of_utc=decision_env["decision_time"],
    )

    assert event.event_type == EventType.DECISION_RECORD
    assert event.test_flag == TestFlag.TEST_ONLY_NON_PROSPECTIVE
    assert event.payload["external_contract_identity"] == PAPER_DECISION_CONTRACT_VERSION
    assert event.payload["is_paper_decision"] is True
    assert event.payload["qualification_result"] is True
    assert event.payload["disqualification_reasons"] == []

    # Verify economic separation
    # 1. Information filter passed: model_p_draw (0.32) > benchmark_market_p_draw (~0.28)
    assert event.payload["model_p_draw"] == 0.32
    assert event.payload["benchmark_market_p_draw"] < 0.32

    # 2. Executable EV calculated on Bet365 odds (3.60): 0.32 * 3.60 - 1 = 0.152
    expected_ev = 0.32 * 3.60 - 1.0
    assert event.payload["execution_ev"] == round(expected_ev, 6)
    assert event.payload["execution_bookmaker"] == "Bet365"
    assert event.payload["execution_decimal_odds"] == 3.60

    # 3. Position sizing: Kelly multiplier = 0.15
    f_star = kelly_fraction(p=0.32, odds=3.60, c=0.15)
    assert event.payload["unconstrained_kelly_fraction"] == round(f_star, 6)
    assert event.payload["constrained_paper_stake_fraction"] == round(f_star, 6)
    assert event.payload["constrained_paper_stake_fraction"] <= 0.025


def test_disqualification_on_information_filter_failure(decision_env):
    """When model draw probability <= benchmark de-vigged draw probability, decision fails with INFORMATION_FILTER_FAILED."""
    daemon = decision_env["daemon"]
    # Model draw prob = 0.25, while benchmark de-vigged draw prob is ~0.28
    weak_pred = _create_synthetic_prediction_event("fix_E0_synth_01", p_draw=0.25)

    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=weak_pred,
        benchmark_event=decision_env["bm_event"],
        execution_event=decision_env["exec_event"],
        as_of_utc=decision_env["decision_time"],
    )

    assert event.payload["qualification_result"] is False
    assert DecisionFailureReason.INFORMATION_FILTER_FAILED.value in event.payload["disqualification_reasons"]
    assert event.payload["constrained_paper_stake_fraction"] == 0.0


def test_disqualification_on_ev_below_threshold(decision_env):
    """When execution odds yield EV < min_ev (0.03), decision fails with EV_BELOW_THRESHOLD."""
    daemon = decision_env["daemon"]
    store = decision_env["store"]
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    # Low odds: 3.15 -> EV = 0.32 * 3.15 - 1 = 0.008 < 0.03
    low_odds_event = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_synth_01",
        bookmaker="Pinnacle",
        odds_draw=3.15,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=2),
        as_of_utc=decision_env["decision_time"],
    )

    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=low_odds_event,
        as_of_utc=decision_env["decision_time"],
    )

    assert event.payload["qualification_result"] is False
    assert DecisionFailureReason.EV_BELOW_THRESHOLD.value in event.payload["disqualification_reasons"]
    assert event.payload["constrained_paper_stake_fraction"] == 0.0


def test_disqualification_on_missing_execution_quote_and_no_fallback(decision_env):
    """Missing execution quote fails with NO_EXECUTION_QUOTE and NEVER falls back to benchmark odds."""
    daemon = decision_env["daemon"]
    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=None,  # No execution quote
        as_of_utc=decision_env["decision_time"],
    )

    assert event.payload["qualification_result"] is False
    assert DecisionFailureReason.NO_EXECUTION_QUOTE.value in event.payload["disqualification_reasons"]
    assert event.payload["execution_decimal_odds"] is None
    assert event.payload["execution_bookmaker"] is None
    assert event.payload["constrained_paper_stake_fraction"] == 0.0


def test_disqualification_on_ineligible_execution_quote(decision_env):
    """Ineligible execution quote (e.g. consensus) causes decision failure with 0 stake."""
    daemon = decision_env["daemon"]
    store = decision_env["store"]
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    consensus_event = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_synth_01",
        bookmaker="consensus",
        odds_draw=3.60,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=2),
        as_of_utc=decision_env["decision_time"],
    )

    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=consensus_event,
        as_of_utc=decision_env["decision_time"],
    )

    assert event.payload["qualification_result"] is False
    assert event.payload["constrained_paper_stake_fraction"] == 0.0


def test_post_cutoff_decision_rejected(decision_env):
    """Decision attempted at or after lead-time cutoff is rejected with POST_CUTOFF_DECISION_ATTEMPT."""
    daemon = decision_env["daemon"]
    sched_kickoff = decision_env["sched_kickoff"]  # 15:00
    cutoff = sched_kickoff - timedelta(hours=1)    # 14:00
    late_decision_time = cutoff + timedelta(minutes=5)  # 14:05 (late)

    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=decision_env["exec_event"],
        as_of_utc=late_decision_time,
    )

    assert event.payload["qualification_result"] is False
    assert DecisionFailureReason.POST_CUTOFF_DECISION_ATTEMPT.value in event.payload["disqualification_reasons"]
    assert event.payload["constrained_paper_stake_fraction"] == 0.0


@pytest.mark.parametrize("bad_state", [
    FixtureLifecycleState.POSTPONED,
    FixtureLifecycleState.STARTED,
    FixtureLifecycleState.COMPLETED,
    FixtureLifecycleState.ABANDONED_PENDING_RULING,
    FixtureLifecycleState.OFFICIAL_RESULT_STANDS,
    FixtureLifecycleState.VOIDED,
])
def test_ineligible_fixture_lifecycle_states(decision_env, bad_state):
    """Fixtures not in SCHEDULED or RESCHEDULED state are disqualified."""
    daemon = decision_env["daemon"]
    bad_proj = _create_synthetic_fixture_projection(
        scheduled_kickoff=decision_env["sched_kickoff"],
        state=bad_state,
    )

    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=bad_proj,
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=decision_env["exec_event"],
        as_of_utc=decision_env["decision_time"],
    )

    assert event.payload["qualification_result"] is False
    assert any("FIXTURE_LIFECYCLE_INELIGIBLE" in r for r in event.payload["disqualification_reasons"])
    assert event.payload["constrained_paper_stake_fraction"] == 0.0


def test_causal_ordering_violation_rejected(decision_env):
    """Quote timestamp after decision timestamp causes CAUSAL_ORDERING_VIOLATION disqualification."""
    daemon = decision_env["daemon"]
    store = decision_env["store"]
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    decision_time = decision_env["decision_time"]
    future_quote_ts = decision_time + timedelta(minutes=2)

    future_quote_event = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_synth_01",
        bookmaker="Bet365",
        odds_draw=3.60,
        provider_quote_timestamp_utc=future_quote_ts,
        as_of_utc=future_quote_ts,
    )

    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=future_quote_event,
        as_of_utc=decision_time,
    )

    assert event.payload["qualification_result"] is False
    assert DecisionFailureReason.CAUSAL_ORDERING_VIOLATION.value in event.payload["disqualification_reasons"]


def test_single_match_cap_enforcement(decision_env):
    """When unconstrained Kelly fraction exceeds single_match_cap (0.025), stake is clipped to 0.025."""
    daemon = decision_env["daemon"]
    store = decision_env["store"]
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    # Massive edge: p_draw = 0.50, odds = 5.0
    big_pred = _create_synthetic_prediction_event("fix_E0_synth_01", p_draw=0.50)
    huge_odds_event = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_synth_01",
        bookmaker="Pinnacle",
        odds_draw=5.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=2),
        as_of_utc=decision_env["decision_time"],
    )

    # b = 4.0, p = 0.50, q = 0.50. (4*0.5 - 0.5)/4 = 1.5/4 = 0.375.
    # c = 0.15 => f* = 0.15 * 0.375 = 0.05625 > 0.025
    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=big_pred,
        benchmark_event=decision_env["bm_event"],
        execution_event=huge_odds_event,
        as_of_utc=decision_env["decision_time"],
    )

    assert event.payload["qualification_result"] is True
    assert event.payload["unconstrained_kelly_fraction"] > 0.025
    assert event.payload["constrained_paper_stake_fraction"] == 0.025
    assert event.payload["single_match_cap"] == 0.025


def test_daily_slate_cap_and_exposure_snapshot_tracking(decision_env):
    """Daily slate cap (0.08) clips stake across sequential decisions on the same slate."""
    daemon = decision_env["daemon"]
    store = decision_env["store"]
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    # Create 4 fixtures on same slate (2026-09-26) with large stakes that each want 0.025
    decisions = []
    slate_id = "2026-09-26"

    for i in range(1, 5):
        fix_id = f"fix_E0_synth_{i:02d}"
        proj = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
        pred = _create_synthetic_prediction_event(fix_id, p_draw=0.50)
        q_event = exec_daemon.capture_execution_quote(
            internal_fixture_id=fix_id,
            bookmaker="Bet365",
            odds_draw=5.0,
            provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
            as_of_utc=decision_env["decision_time"],
        )

        dec = daemon.evaluate_decision(
            internal_fixture_id=fix_id,
            fixture_projection=proj,
            prediction_event=pred,
            benchmark_event=decision_env["bm_event"],
            execution_event=q_event,
            as_of_utc=decision_env["decision_time"] + timedelta(seconds=i),
            slate_id=slate_id,
            existing_decisions=decisions,
        )
        decisions.append(dec)

    # Match 1: prior = 0.0, gets 0.025, post = 0.025
    assert decisions[0].payload["constrained_paper_stake_fraction"] == 0.025
    assert decisions[0].payload["exposure_snapshot"]["prior_cumulative_exposure"] == 0.0
    assert decisions[0].payload["exposure_snapshot"]["post_decision_cumulative_exposure"] == 0.025

    # Match 2: prior = 0.025, gets 0.025, post = 0.050
    assert decisions[1].payload["constrained_paper_stake_fraction"] == 0.025
    assert decisions[1].payload["exposure_snapshot"]["prior_cumulative_exposure"] == 0.025
    assert decisions[1].payload["exposure_snapshot"]["post_decision_cumulative_exposure"] == 0.050

    # Match 3: prior = 0.050, gets 0.025, post = 0.075
    assert decisions[2].payload["constrained_paper_stake_fraction"] == 0.025
    assert decisions[2].payload["exposure_snapshot"]["prior_cumulative_exposure"] == 0.050
    assert decisions[2].payload["exposure_snapshot"]["post_decision_cumulative_exposure"] == 0.075

    # Match 4: prior = 0.075, daily cap is 0.080, remaining = 0.005! Stake clipped to 0.005!
    assert decisions[3].payload["constrained_paper_stake_fraction"] == 0.005
    assert decisions[3].payload["exposure_snapshot"]["prior_cumulative_exposure"] == 0.075
    assert decisions[3].payload["exposure_snapshot"]["post_decision_cumulative_exposure"] == 0.080

    # Match 5: cap exhausted (0.080), remaining = 0.0! Stake is 0.0!
    fix_id_5 = "fix_E0_synth_05"
    q_event_5 = exec_daemon.capture_execution_quote(
        internal_fixture_id=fix_id_5,
        bookmaker="Bet365",
        odds_draw=5.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
        as_of_utc=decision_env["decision_time"],
    )
    dec_5 = daemon.evaluate_decision(
        internal_fixture_id=fix_id_5,
        fixture_projection=proj,
        prediction_event=pred,
        benchmark_event=decision_env["bm_event"],
        execution_event=q_event_5,
        as_of_utc=decision_env["decision_time"] + timedelta(seconds=10),
        slate_id=slate_id,
        existing_decisions=decisions,
    )
    assert dec_5.payload["constrained_paper_stake_fraction"] == 0.0
    assert dec_5.payload["exposure_snapshot"]["capacity_exhausted"] is True
    assert DecisionFailureReason.SLATE_CAPACITY_EXHAUSTED.value in dec_5.payload["disqualification_reasons"]


def test_deterministic_slate_replay(decision_env):
    """Replaying identical sequence of slate decisions produces identical stakes and exposure values."""
    timing = TimingContract(decision_lead_time_seconds=3600)
    store1 = ImmutableEventStore()
    daemon1 = DecisionDaemon(event_store=store1, timing_contract=timing)
    exec_daemon1 = ExecutionQuoteDaemon(event_store=store1)

    store2 = ImmutableEventStore()
    daemon2 = DecisionDaemon(event_store=store2, timing_contract=timing)
    exec_daemon2 = ExecutionQuoteDaemon(event_store=store2)

    slate_id = "2026-09-26"
    decisions_pass_1 = []
    decisions_pass_2 = []
    q_events_pass_1 = []

    for i in range(1, 4):
        fix_id = f"fix_E0_replay_{i}"
        proj = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
        pred = _create_synthetic_prediction_event(fix_id, p_draw=0.40)

        q_event1 = exec_daemon1.capture_execution_quote(
            internal_fixture_id=fix_id,
            bookmaker="Bet365",
            odds_draw=4.0,
            provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
            as_of_utc=decision_env["decision_time"],
        )
        q_events_pass_1.append(q_event1)
        d1 = daemon1.evaluate_decision(
            internal_fixture_id=fix_id,
            fixture_projection=proj,
            prediction_event=pred,
            benchmark_event=decision_env["bm_event"],
            execution_event=q_event1,
            as_of_utc=decision_env["decision_time"] + timedelta(seconds=i),
            slate_id=slate_id,
            existing_decisions=decisions_pass_1,
        )
        decisions_pass_1.append(d1)

        q_event2 = exec_daemon2.capture_execution_quote(
            internal_fixture_id=fix_id,
            bookmaker="Bet365",
            odds_draw=4.0,
            provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
            as_of_utc=decision_env["decision_time"],
        )
        d2 = daemon2.evaluate_decision(
            internal_fixture_id=fix_id,
            fixture_projection=proj,
            prediction_event=pred,
            benchmark_event=decision_env["bm_event"],
            execution_event=q_event2,
            as_of_utc=decision_env["decision_time"] + timedelta(seconds=i),
            slate_id=slate_id,
            existing_decisions=decisions_pass_2,
        )
        decisions_pass_2.append(d2)

    for d1, d2 in zip(decisions_pass_1, decisions_pass_2):
        assert d1.payload["constrained_paper_stake_fraction"] == d2.payload["constrained_paper_stake_fraction"]
        assert d1.payload["exposure_snapshot"] == d2.payload["exposure_snapshot"]
        assert d1.payload["decision_id"] == d2.payload["decision_id"]
        assert d1.idempotency_key == d2.idempotency_key

    # Also verify idempotent append against store1
    d1_replayed = daemon1.evaluate_decision(
        internal_fixture_id="fix_E0_replay_1",
        fixture_projection=_create_synthetic_fixture_projection(decision_env["sched_kickoff"]),
        prediction_event=_create_synthetic_prediction_event("fix_E0_replay_1", p_draw=0.40),
        benchmark_event=decision_env["bm_event"],
        execution_event=q_events_pass_1[0],
        as_of_utc=decision_env["decision_time"] + timedelta(seconds=1),
        slate_id=slate_id,
        existing_decisions=[],
    )
    assert d1_replayed.event_id == decisions_pass_1[0].event_id


def test_prospective_decision_activation_blocked():
    """Attempting prospective decision evaluation raises ProspectiveActivationBlockedError when pre-epoch gates unpassed."""
    with pytest.raises(ProspectiveActivationBlockedError):
        DecisionDaemon(
            capture_mode=CaptureMode.PROSPECTIVE,
            epoch_model_sha=None,
        )


# ==============================================================================
# 1 — Semantic Config Identity Tests (Requirement 1)
# ==============================================================================

def test_a09_semantic_identity_differs_from_stage3():
    """Stage 4 A09 semantic config identity must differ from previous Stage 3 identity."""
    from src.validation.manifest import get_semantic_config_identity
    stage3_baseline = "ab270642b8818a83b9a42a4b6bec4b5002db9958a86b6caf49e23feb7dfb70ed"
    current_hash = get_semantic_config_identity()
    assert current_hash != stage3_baseline
    assert len(current_hash) == 64


def test_execution_price_source_changes_semantic_identity():
    """Changing execution_price_source must change semantic config identity."""
    from src.validation.manifest import get_semantic_config_identity
    from src.config import get_config
    base_cfg = get_config().model_dump()
    h1 = get_semantic_config_identity(config=base_cfg)

    cfg2 = get_config().model_dump()
    cfg2["execution"]["execution_price_source"] = "CONSENSUS_COMPOSITE"
    h2 = get_semantic_config_identity(config=cfg2)
    assert h1 != h2


def test_kelly_multiplier_changes_semantic_identity():
    """Changing kelly_multiplier / kelly_fraction must change semantic config identity."""
    from src.validation.manifest import get_semantic_config_identity
    from src.config import get_config
    base_cfg = get_config().model_dump()
    h1 = get_semantic_config_identity(config=base_cfg)

    cfg2 = get_config().model_dump()
    cfg2["edge"]["kelly_fraction"] = 0.20
    h2 = get_semantic_config_identity(config=cfg2)
    assert h1 != h2


def test_ev_hurdle_changes_semantic_identity():
    """Changing min_ev hurdle must change semantic config identity."""
    from src.validation.manifest import get_semantic_config_identity
    from src.config import get_config
    base_cfg = get_config().model_dump()
    h1 = get_semantic_config_identity(config=base_cfg)

    cfg2 = get_config().model_dump()
    cfg2["edge"]["min_ev"] = 0.05
    h2 = get_semantic_config_identity(config=cfg2)
    assert h1 != h2


def test_single_match_cap_changes_semantic_identity():
    """Changing single_match_cap must change semantic config identity."""
    from src.validation.manifest import get_semantic_config_identity
    from src.config import get_config
    base_cfg = get_config().model_dump()
    h1 = get_semantic_config_identity(config=base_cfg)

    cfg2 = get_config().model_dump()
    cfg2["edge"]["single_match_cap"] = 0.035
    h2 = get_semantic_config_identity(config=cfg2)
    assert h1 != h2


def test_slate_cap_changes_semantic_identity():
    """Changing daily_slate_cap must change semantic config identity."""
    from src.validation.manifest import get_semantic_config_identity
    from src.config import get_config
    base_cfg = get_config().model_dump()
    h1 = get_semantic_config_identity(config=base_cfg)

    cfg2 = get_config().model_dump()
    cfg2["edge"]["daily_slate_cap"] = 0.12
    h2 = get_semantic_config_identity(config=cfg2)
    assert h1 != h2


def test_execution_freshness_changes_semantic_identity():
    """Changing max_execution_quote_age_seconds must change semantic config identity."""
    from src.validation.manifest import get_semantic_config_identity
    from src.config import get_config
    base_cfg = get_config().model_dump()
    h1 = get_semantic_config_identity(config=base_cfg)

    cfg2 = get_config().model_dump()
    cfg2["execution"]["max_execution_quote_age_seconds"] = 600
    h2 = get_semantic_config_identity(config=cfg2)
    assert h1 != h2


# ==============================================================================
# 2 — Authoritative Slate Exposure Tests (Requirement 2)
# ==============================================================================

def test_slate_exposure_derived_from_event_store(decision_env):
    """Decision exposure is derived authoritatively from event store without requiring existing_decisions."""
    store = ImmutableEventStore()
    daemon = DecisionDaemon(event_store=store, timing_contract=decision_env["daemon"].timing_contract)
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    slate_id = "2026-09-26"

    # Match 1: evaluated and stored in event store
    proj1 = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
    pred1 = _create_synthetic_prediction_event("fix_E0_auth_1", p_draw=0.40)
    q1 = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_auth_1",
        bookmaker_id="bet365",
        odds_draw=4.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
        as_of_utc=decision_env["decision_time"],
    )
    d1 = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_auth_1",
        fixture_projection=proj1,
        prediction_event=pred1,
        benchmark_event=decision_env["bm_event"],
        execution_event=q1,
        as_of_utc=decision_env["decision_time"],
        slate_id=slate_id,
        existing_decisions=None,  # No caller-supplied list
    )
    assert d1.payload["constrained_paper_stake_fraction"] == 0.025

    # Match 2: evaluated with existing_decisions=None. MUST authoritatively detect match 1 from event store!
    proj2 = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
    pred2 = _create_synthetic_prediction_event("fix_E0_auth_2", p_draw=0.40)
    q2 = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_auth_2",
        bookmaker_id="bet365",
        odds_draw=4.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
        as_of_utc=decision_env["decision_time"],
    )
    d2 = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_auth_2",
        fixture_projection=proj2,
        prediction_event=pred2,
        benchmark_event=decision_env["bm_event"],
        execution_event=q2,
        as_of_utc=decision_env["decision_time"] + timedelta(seconds=1),
        slate_id=slate_id,
        existing_decisions=None,  # Derived strictly from store!
    )
    assert d2.payload["exposure_snapshot"]["prior_cumulative_paper_exposure"] == 0.025
    assert d2.payload["exposure_snapshot"]["post_decision_cumulative_exposure"] == 0.050
    assert d1.payload["decision_id"] in d2.payload["exposure_snapshot"]["prior_qualifying_decision_ids"]


def test_caller_cannot_omit_prior_decision(decision_env):
    """Caller supplying incomplete existing_decisions cannot omit stored prior decisions."""
    store = ImmutableEventStore()
    daemon = DecisionDaemon(event_store=store, timing_contract=decision_env["daemon"].timing_contract)
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    slate_id = "2026-09-26"
    proj1 = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
    pred1 = _create_synthetic_prediction_event("fix_E0_auth_1", p_draw=0.40)
    q1 = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_auth_1",
        bookmaker_id="bet365",
        odds_draw=4.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
        as_of_utc=decision_env["decision_time"],
    )
    daemon.evaluate_decision(
        internal_fixture_id="fix_E0_auth_1",
        fixture_projection=proj1,
        prediction_event=pred1,
        benchmark_event=decision_env["bm_event"],
        execution_event=q1,
        as_of_utc=decision_env["decision_time"],
        slate_id=slate_id,
    )

    # Caller attempts to pass empty existing_decisions=[] to bypass prior exposure
    proj2 = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
    pred2 = _create_synthetic_prediction_event("fix_E0_auth_2", p_draw=0.40)
    q2 = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_auth_2",
        bookmaker_id="bet365",
        odds_draw=4.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
        as_of_utc=decision_env["decision_time"],
    )

    with pytest.raises(ValueError, match="Authoritative exposure violation"):
        daemon.evaluate_decision(
            internal_fixture_id="fix_E0_auth_2",
            fixture_projection=proj2,
            prediction_event=pred2,
            benchmark_event=decision_env["bm_event"],
            execution_event=q2,
            as_of_utc=decision_env["decision_time"] + timedelta(seconds=1),
            slate_id=slate_id,
            existing_decisions=[],  # Omission attempt!
        )


def test_caller_cannot_inject_fake_prior_decision(decision_env):
    """Caller supplying fake prior decisions not in event store is rejected fail-closed."""
    store = ImmutableEventStore()
    daemon = DecisionDaemon(event_store=store, timing_contract=decision_env["daemon"].timing_contract)
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    slate_id = "2026-09-26"
    proj = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
    pred = _create_synthetic_prediction_event("fix_E0_auth_1", p_draw=0.40)
    q = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_auth_1",
        bookmaker_id="bet365",
        odds_draw=4.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
        as_of_utc=decision_env["decision_time"],
    )

    fake_decision = ProspectiveEvent(
        event_id="evt_fake_dec",
        event_type=EventType.DECISION_RECORD,
        epoch_id="PRE_EPOCH_STAGE_4",
        idempotency_key="f" * 64,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={
            "decision_id": "dec_fake",
            "qualification_result": True,
            "constrained_paper_stake_fraction": 0.05,
            "exposure_snapshot": {"slate_id": slate_id},
        },
    )

    with pytest.raises(ValueError, match="Authoritative exposure violation"):
        daemon.evaluate_decision(
            internal_fixture_id="fix_E0_auth_1",
            fixture_projection=proj,
            prediction_event=pred,
            benchmark_event=decision_env["bm_event"],
            execution_event=q,
            as_of_utc=decision_env["decision_time"],
            slate_id=slate_id,
            existing_decisions=[fake_decision],  # Fake injection attempt!
        )


def test_exposure_state_identity_deterministic():
    """EXPOSURE_STATE_IDENTITY is deterministic and changes with any decision ID or stake change."""
    from src.workers.decision_daemon import compute_exposure_state_identity
    h1 = compute_exposure_state_identity("2026-09-26", [("dec_1", 0.025), ("dec_2", 0.025)])
    h2 = compute_exposure_state_identity("2026-09-26", [("dec_1", 0.025), ("dec_2", 0.025)])
    assert h1 == h2
    assert len(h1) == 64

    # Different stake
    h3 = compute_exposure_state_identity("2026-09-26", [("dec_1", 0.025), ("dec_2", 0.020)])
    assert h1 != h3

    # Different ID
    h4 = compute_exposure_state_identity("2026-09-26", [("dec_1", 0.025), ("dec_3", 0.025)])
    assert h1 != h4

    # Different slate
    h5 = compute_exposure_state_identity("2026-09-27", [("dec_1", 0.025), ("dec_2", 0.025)])
    assert h1 != h5


def test_same_decision_replay_uses_same_exposure_state(decision_env):
    """Replaying decision evaluation uses bitwise identical exposure state identity."""
    store = ImmutableEventStore()
    daemon = DecisionDaemon(event_store=store, timing_contract=decision_env["daemon"].timing_contract)
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    slate_id = "2026-09-26"
    proj = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
    pred = _create_synthetic_prediction_event("fix_E0_rep_1", p_draw=0.40)
    q = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_rep_1",
        bookmaker_id="bet365",
        odds_draw=4.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
        as_of_utc=decision_env["decision_time"],
    )

    d1 = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_rep_1",
        fixture_projection=proj,
        prediction_event=pred,
        benchmark_event=decision_env["bm_event"],
        execution_event=q,
        as_of_utc=decision_env["decision_time"],
        slate_id=slate_id,
    )

    d2 = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_rep_1",
        fixture_projection=proj,
        prediction_event=pred,
        benchmark_event=decision_env["bm_event"],
        execution_event=q,
        as_of_utc=decision_env["decision_time"],
        slate_id=slate_id,
    )

    assert d1.payload["exposure_state_identity"] == d2.payload["exposure_state_identity"]
    assert d1.event_id == d2.event_id


def test_slate_cap_cannot_be_bypassed_by_incomplete_input(decision_env):
    """Authoritative store enforces daily slate cap regardless of incomplete caller input."""
    store = ImmutableEventStore()
    daemon = DecisionDaemon(event_store=store, timing_contract=decision_env["daemon"].timing_contract)
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    slate_id = "2026-09-26"
    # Fill slate cap to 0.08 with 3 decisions (0.025 + 0.025 + 0.025 = 0.075)
    for i in range(1, 4):
        f_id = f"fix_E0_fill_{i}"
        proj = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
        pred = _create_synthetic_prediction_event(f_id, p_draw=0.50)
        q = exec_daemon.capture_execution_quote(
            internal_fixture_id=f_id,
            bookmaker_id="bet365",
            odds_draw=5.0,
            provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
            as_of_utc=decision_env["decision_time"],
        )
        daemon.evaluate_decision(
            internal_fixture_id=f_id,
            fixture_projection=proj,
            prediction_event=pred,
            benchmark_event=decision_env["bm_event"],
            execution_event=q,
            as_of_utc=decision_env["decision_time"] + timedelta(seconds=i),
            slate_id=slate_id,
        )

    # 4th match: remaining capacity is exactly 0.005
    f_id_4 = "fix_E0_fill_4"
    proj4 = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
    pred4 = _create_synthetic_prediction_event(f_id_4, p_draw=0.50)
    q4 = exec_daemon.capture_execution_quote(
        internal_fixture_id=f_id_4,
        bookmaker_id="bet365",
        odds_draw=5.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=5),
        as_of_utc=decision_env["decision_time"],
    )
    d4 = daemon.evaluate_decision(
        internal_fixture_id=f_id_4,
        fixture_projection=proj4,
        prediction_event=pred4,
        benchmark_event=decision_env["bm_event"],
        execution_event=q4,
        as_of_utc=decision_env["decision_time"] + timedelta(seconds=4),
        slate_id=slate_id,
    )
    assert d4.payload["constrained_paper_stake_fraction"] == 0.005


def test_decision_ordering_is_causal(decision_env):
    """Decisions in the future relative to current decision are not counted in prior exposure."""
    store = ImmutableEventStore()
    daemon = DecisionDaemon(event_store=store, timing_contract=decision_env["daemon"].timing_contract)
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    slate_id = "2026-09-26"
    t_now = decision_env["decision_time"]

    # Past decision (evaluated at t_now - 10 min): quote & prediction at t_now - 15 min
    proj1 = _create_synthetic_fixture_projection(decision_env["sched_kickoff"])
    pred1 = _create_synthetic_prediction_event("fix_E0_causal_1", p_draw=0.40, prediction_time=t_now - timedelta(minutes=15))
    bm_daemon = MarketBenchmarkDaemon(event_store=store)
    bm1 = bm_daemon.capture_benchmark_quote(
        internal_fixture_id="fix_E0_causal_1",
        source="the_odds_api_consensus",
        odds_home=2.5,
        odds_draw=3.5,
        odds_away=3.0,
        provider_quote_timestamp_utc=t_now - timedelta(minutes=15),
        as_of_utc=t_now - timedelta(minutes=10),
    )
    q1 = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_causal_1",
        bookmaker_id="bet365",
        odds_draw=4.0,
        provider_quote_timestamp_utc=t_now - timedelta(minutes=15),
        as_of_utc=t_now - timedelta(minutes=10),
    )
    d_past = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_causal_1",
        fixture_projection=proj1,
        prediction_event=pred1,
        benchmark_event=bm1,
        execution_event=q1,
        as_of_utc=t_now - timedelta(minutes=10),
        slate_id=slate_id,
    )
    assert d_past.payload["qualification_result"] is True
    assert d_past.payload["constrained_paper_stake_fraction"] > 0.0

    # Future decision (evaluated at t_now + 10 min):
    pred2 = _create_synthetic_prediction_event("fix_E0_causal_2", p_draw=0.40, prediction_time=t_now + timedelta(minutes=5))
    bm2 = bm_daemon.capture_benchmark_quote(
        internal_fixture_id="fix_E0_causal_2",
        source="the_odds_api_consensus",
        odds_home=2.5,
        odds_draw=3.5,
        odds_away=3.0,
        provider_quote_timestamp_utc=t_now + timedelta(minutes=5),
        as_of_utc=t_now + timedelta(minutes=10),
    )
    q2 = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_causal_2",
        bookmaker_id="bet365",
        odds_draw=4.0,
        provider_quote_timestamp_utc=t_now + timedelta(minutes=5),
        as_of_utc=t_now + timedelta(minutes=10),
    )
    d_future = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_causal_2",
        fixture_projection=proj1,
        prediction_event=pred2,
        benchmark_event=bm2,
        execution_event=q2,
        as_of_utc=t_now + timedelta(minutes=10),
        slate_id=slate_id,
    )
    assert d_future.payload["qualification_result"] is True

    # At t_now: only past decision is in authoritative prior set; future decision is excluded
    prior_at_t_now = daemon._derive_authoritative_prior_decisions(slate_id, t_now)
    assert len(prior_at_t_now) == 1
    assert prior_at_t_now[0].payload["internal_fixture_id"] == "fix_E0_causal_1"

    # At t_now + 15 min: both are included
    prior_at_t_future = daemon._derive_authoritative_prior_decisions(slate_id, t_now + timedelta(minutes=15))
    assert len(prior_at_t_future) == 2


# ==============================================================================
# 5 & 6 — Selection Identity & Negative EV Tests (Requirements 5 & 6)
# ==============================================================================

def test_selection_mismatch_rejected(decision_env):
    """When execution quote selection does not match decision target (DRAW), decision is disqualified with SELECTION_MISMATCH."""
    daemon = decision_env["daemon"]
    store = decision_env["store"]
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    # Capture quote explicitly for HOME
    home_quote = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_synth_01",
        bookmaker_id="bet365",
        market_type="1X2",
        selection="HOME",
        odds_home=2.20,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=2),
        as_of_utc=decision_env["decision_time"],
    )

    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=home_quote,
        as_of_utc=decision_env["decision_time"],
    )

    assert event.payload["qualification_result"] is False
    assert DecisionFailureReason.SELECTION_MISMATCH.value in event.payload["disqualification_reasons"]
    assert event.payload["constrained_paper_stake_fraction"] == 0.0


def test_home_quote_cannot_size_draw_decision(decision_env):
    """Home odds cannot be used to size a draw decision."""
    daemon = decision_env["daemon"]
    store = decision_env["store"]
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    # Home quote with high odds
    home_quote = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_synth_01",
        bookmaker_id="pinnacle",
        market_type="1X2",
        selection="HOME",
        odds_home=5.50,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=2),
        as_of_utc=decision_env["decision_time"],
    )

    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=home_quote,
        as_of_utc=decision_env["decision_time"],
    )
    assert event.payload["qualification_result"] is False
    assert event.payload["constrained_paper_stake_fraction"] == 0.0


def test_negative_ev_preserved_but_zero_stake(decision_env):
    """Negative EV is faithfully preserved in evidence (not clamped to 0), and stake is strictly 0.0."""
    daemon = decision_env["daemon"]
    store = decision_env["store"]
    exec_daemon = ExecutionQuoteDaemon(event_store=store)

    # Model p_draw = 0.32, execution odds = 2.0 -> EV = 0.32 * 2.0 - 1 = -0.36
    bad_quote = exec_daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_synth_01",
        bookmaker_id="bet365",
        odds_draw=2.0,
        provider_quote_timestamp_utc=decision_env["decision_time"] - timedelta(minutes=2),
        as_of_utc=decision_env["decision_time"],
    )

    event = daemon.evaluate_decision(
        internal_fixture_id="fix_E0_synth_01",
        fixture_projection=decision_env["proj"],
        prediction_event=decision_env["pred_event"],
        benchmark_event=decision_env["bm_event"],
        execution_event=bad_quote,
        as_of_utc=decision_env["decision_time"],
    )

    assert event.payload["qualification_result"] is False
    assert event.payload["execution_ev"] == -0.36  # Faithfully preserved negative EV!
    assert event.payload["constrained_paper_stake_fraction"] == 0.0
    assert DecisionFailureReason.EV_BELOW_THRESHOLD.value in event.payload["disqualification_reasons"]
