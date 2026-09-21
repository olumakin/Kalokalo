"""
Unit and adversarial test suite for Named Execution Feed (Stage 4 / A09).

Verifies ExecutionQuoteContract::v1.0 invariants:
- Named bookmaker identity (prohibition of consensus/synthetic/anonymous sources)
- Provider quote timestamp requirement (no ingestion timestamp substitution)
- Quote freshness and age limits (max_quote_age_seconds)
- Causal ordering and clock anomaly protection (>120s future, post-kickoff)
- Odds validity (strictly > 1.0, non-nan, finite)
- Append-only event store persistence and idempotency
- Strict separation from benchmark quotes (is_executable_price = True vs False)
- Non-prospective tagging and activation blocking
"""
from datetime import datetime, timedelta, timezone
import math
import pytest

from src.ingestion.execution_feed import (
    EXECUTION_QUOTE_CONTRACT_VERSION,
    ExecutionFailureReason,
    ExecutionQuoteDaemon,
    is_valid_named_bookmaker,
)
from src.tracking.events import (
    DuplicateIdempotencyCollisionError,
    EventType,
    ImmutableEventStore,
    TestFlag,
)
from src.validation.manifest import EXTERNAL_CONTRACT_IDENTITIES
from src.workers import (
    CaptureMode,
    ProspectiveActivationBlockedError,
    ProspectiveActivationContext,
)
from src.workers.market_daemon import MarketBenchmarkDaemon


def test_contract_registered_in_external_contract_identities():
    """ExecutionQuoteContract::v1.0 must be registered in EXTERNAL_CONTRACT_IDENTITIES."""
    assert "execution_quote_contract" in EXTERNAL_CONTRACT_IDENTITIES
    assert EXTERNAL_CONTRACT_IDENTITIES["execution_quote_contract"] == EXECUTION_QUOTE_CONTRACT_VERSION


@pytest.mark.parametrize(
    "valid_name",
    [
        "Bet365",
        "Pinnacle",
        "WilliamHill",
        "Betfair",
        "Unibet",
        "Betway",
        "BoyleSports",
    ],
)
def test_valid_named_bookmaker_accepted(valid_name):
    """Named observable bookmakers are accepted."""
    assert is_valid_named_bookmaker(valid_name) is True


@pytest.mark.parametrize(
    "invalid_name",
    [
        None,
        "",
        "   ",
        "consensus",
        "Consensus",
        "CONSENSUS",
        "average",
        "Market Average",
        "median",
        "synthetic",
        "anonymous",
        "market_composite",
        "composite_book",
        "aggregate",
        "fair",
        "reference",
        "pinnacle_devigged",
        "unnamed",
        "unknown",
        "consensus_uk",
        "anonymous_pool",
    ],
)
def test_anonymous_and_composite_sources_rejected(invalid_name):
    """Anonymous, consensus, aggregate, or synthetic sources fail closed."""
    assert is_valid_named_bookmaker(invalid_name) is False


def test_capture_valid_named_execution_quote():
    """Valid execution quote is captured with is_executable_price = True."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    quote_ts = now - timedelta(minutes=5)

    event = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="Bet365",
        odds_draw=3.40,
        odds_home=2.10,
        odds_away=3.60,
        provider_event_id="p_evt_001",
        provider_quote_timestamp_utc=quote_ts,
        as_of_utc=now,
    )

    assert event.event_type == EventType.EXECUTION_QUOTE_SNAPSHOT
    assert event.test_flag == TestFlag.TEST_ONLY_NON_PROSPECTIVE
    assert event.payload["is_executable_price"] is True
    assert event.payload["bookmaker"] == "Bet365"
    assert event.payload["raw_odds_draw"] == 3.40
    assert event.payload["failure_reason"] is None
    assert event.payload["provider_quote_timestamp_status"] == "PRESENT"
    assert event.payload["external_contract_identity"] == EXECUTION_QUOTE_CONTRACT_VERSION
    assert event.payload["prospective_eligible"] is False


def test_capture_rejected_on_consensus_bookmaker():
    """Consensus bookmaker fails closed with EXECUTION_SOURCE_NOT_NAMED and is_executable_price = False."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    event = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="consensus",
        odds_draw=3.40,
        provider_quote_timestamp_utc=now - timedelta(minutes=2),
        as_of_utc=now,
    )

    assert event.payload["is_executable_price"] is False
    assert event.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_SOURCE_NOT_NAMED.value


def test_capture_rejected_on_empty_bookmaker():
    """Empty or None bookmaker fails closed with EXECUTION_SOURCE_NOT_NAMED."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    event = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="",
        odds_draw=3.40,
        provider_quote_timestamp_utc=now - timedelta(minutes=2),
        as_of_utc=now,
    )

    assert event.payload["is_executable_price"] is False
    assert event.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_SOURCE_NOT_NAMED.value


def test_capture_rejected_on_missing_quote_timestamp():
    """Missing provider quote timestamp fails closed with EXECUTION_QUOTE_TIMESTAMP_UNKNOWN and does NOT substitute ingest timestamp."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    event = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="Pinnacle",
        odds_draw=3.40,
        provider_quote_timestamp_utc=None,
        as_of_utc=now,
    )

    assert event.payload["is_executable_price"] is False
    assert event.payload["provider_quote_timestamp_status"] == "UNKNOWN"
    assert event.payload["provider_quote_timestamp_utc"] is None
    assert event.payload["ingestion_timestamp_utc"] == now.isoformat()
    assert event.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_QUOTE_TIMESTAMP_UNKNOWN.value


def test_capture_rejected_on_stale_quote():
    """Quote exceeding max_quote_age_seconds fails closed with EXECUTION_QUOTE_STALE."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store, max_quote_age_seconds=900)  # 15 minutes
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    stale_quote_ts = now - timedelta(minutes=20)  # 1200 seconds old

    event = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="Pinnacle",
        odds_draw=3.40,
        provider_quote_timestamp_utc=stale_quote_ts,
        as_of_utc=now,
    )

    assert event.payload["is_executable_price"] is False
    assert event.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_QUOTE_STALE.value
    assert event.payload["quote_age_seconds"] == 1200.0


def test_capture_rejected_on_future_timestamp_anomaly():
    """Quote with timestamp materially in the future (>120s) fails with EXECUTION_QUOTE_FUTURE_TIMESTAMP."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    future_ts = now + timedelta(seconds=180)  # 3 minutes in future

    event = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="Pinnacle",
        odds_draw=3.40,
        provider_quote_timestamp_utc=future_ts,
        as_of_utc=now,
    )

    assert event.payload["is_executable_price"] is False
    assert event.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_QUOTE_FUTURE_TIMESTAMP.value


def test_capture_rejected_on_post_kickoff_quote():
    """Quote timestamped after actual kickoff fails closed with EXECUTION_QUOTE_POST_KICKOFF."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    kickoff = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
    post_kickoff_quote_ts = kickoff + timedelta(minutes=5)
    now = kickoff + timedelta(minutes=6)

    event = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="Pinnacle",
        odds_draw=3.40,
        provider_quote_timestamp_utc=post_kickoff_quote_ts,
        actual_kickoff_utc=kickoff,
        as_of_utc=now,
    )

    assert event.payload["is_executable_price"] is False
    assert event.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_QUOTE_POST_KICKOFF.value


@pytest.mark.parametrize("bad_odds", [None, 1.0, 0.95, -2.0, float("nan"), float("inf")])
def test_capture_rejected_on_invalid_odds(bad_odds):
    """Invalid odds (<= 1.0, nan, inf, None) fail closed with EXECUTION_ODDS_INVALID."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    event = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="Bet365",
        odds_draw=bad_odds,
        provider_quote_timestamp_utc=now - timedelta(minutes=2),
        as_of_utc=now,
    )

    assert event.payload["is_executable_price"] is False
    assert event.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_ODDS_INVALID.value


def test_execution_quote_idempotency_and_collision():
    """Identical quote is idempotent no-op; conflicting quote payload raises DuplicateIdempotencyCollisionError."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    quote_ts = now - timedelta(minutes=3)

    event1 = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="Bet365",
        odds_draw=3.40,
        provider_quote_timestamp_utc=quote_ts,
        as_of_utc=now,
    )

    # Identical call
    event2 = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_test123",
        bookmaker="Bet365",
        odds_draw=3.40,
        provider_quote_timestamp_utc=quote_ts,
        as_of_utc=now,
    )

    assert event1.event_id == event2.event_id

    # Conflicting call using same idempotency key manually
    from src.tracking.events import ProspectiveEvent
    conflicting = ProspectiveEvent(
        event_type=EventType.EXECUTION_QUOTE_SNAPSHOT,
        epoch_id="PRE_EPOCH_STAGE_4",
        idempotency_key=event1.idempotency_key,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={"internal_fixture_id": "fix_E0_test123", "different": "payload"},
    )
    with pytest.raises(DuplicateIdempotencyCollisionError):
        store.append(conflicting)


def test_benchmark_quote_is_never_executable():
    """Market benchmark quotes have is_executable_price = False and cannot be confused with execution quotes."""
    store = ImmutableEventStore()
    bm_daemon = MarketBenchmarkDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    bm_event = bm_daemon.capture_benchmark_quote(
        internal_fixture_id="fix_E0_test123",
        source="the_odds_api_consensus",
        odds_home=2.10,
        odds_draw=3.40,
        odds_away=3.60,
        provider_quote_timestamp_utc=now - timedelta(minutes=5),
        as_of_utc=now,
    )

    assert bm_event.payload["is_executable_price"] is False
    assert bm_event.event_type == EventType.MARKET_BENCHMARK_SNAPSHOT


def test_prospective_activation_blocked():
    """Attempting prospective execution capture raises ProspectiveActivationBlockedError when gates unpassed."""
    with pytest.raises(ProspectiveActivationBlockedError):
        ExecutionQuoteDaemon(
            capture_mode=CaptureMode.PROSPECTIVE,
            epoch_model_sha=None,
        )


def test_bookmaker_display_name_not_canonical_identity():
    """Changing presentation formatting or display name must not alter canonical bookmaker_id."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    quote_ts = now - timedelta(minutes=5)

    ev1 = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_bkmk_1",
        bookmaker_id="bet365",
        bookmaker_name="Bet365 Official UK",
        odds_draw=3.50,
        provider_quote_timestamp_utc=quote_ts,
        as_of_utc=now,
    )
    assert ev1.payload["bookmaker_id"] == "bet365"
    assert ev1.payload["bookmaker_name"] == "Bet365 Official UK"

    ev2 = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_bkmk_2",
        bookmaker_id="BET365",
        bookmaker_name="bet365 Sportsbook",
        odds_draw=3.50,
        provider_quote_timestamp_utc=quote_ts,
        as_of_utc=now,
    )
    assert ev2.payload["bookmaker_id"] == "bet365"
    assert ev2.payload["bookmaker_name"] == "bet365 Sportsbook"


def test_execution_quote_requires_stable_bookmaker_id():
    """Quotes missing a stable bookmaker_id or with anonymous/consensus IDs fail fail-closed."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    quote_ts = now - timedelta(minutes=2)

    ev_empty = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_bkmk_3",
        bookmaker_id="",
        odds_draw=3.50,
        provider_quote_timestamp_utc=quote_ts,
        as_of_utc=now,
    )
    assert ev_empty.payload["is_executable_price"] is False
    assert ev_empty.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_SOURCE_NOT_NAMED.value

    ev_anon = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_bkmk_4",
        bookmaker_id="consensus",
        bookmaker_name="Consensus Market Average",
        odds_draw=3.50,
        provider_quote_timestamp_utc=quote_ts,
        as_of_utc=now,
    )
    assert ev_anon.payload["is_executable_price"] is False
    assert ev_anon.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_SOURCE_NOT_NAMED.value


def test_execution_selection_identity_preserved():
    """Execution quote must explicitly preserve market_type=1X2 and selection=DRAW."""
    store = ImmutableEventStore()
    daemon = ExecutionQuoteDaemon(event_store=store)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    quote_ts = now - timedelta(minutes=2)

    ev = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_sel_1",
        bookmaker_id="pinnacle",
        bookmaker_name="Pinnacle",
        market_type="1X2",
        selection="DRAW",
        odds_draw=3.45,
        provider_quote_timestamp_utc=quote_ts,
        as_of_utc=now,
    )
    assert ev.payload["market_type"] == "1X2"
    assert ev.payload["selection"] == "DRAW"
    assert ev.payload["selection_odds"] == 3.45
    assert ev.payload["is_executable_price"] is True


def test_freshness_exact_boundary_behavior():
    """Verify exact boundary behavior: age < threshold (valid), age == threshold (valid), age > threshold (stale)."""
    store = ImmutableEventStore()
    threshold_seconds = 900
    daemon = ExecutionQuoteDaemon(event_store=store, max_quote_age_seconds=threshold_seconds)
    now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)

    # 1. age < threshold (899.0s) -> valid
    q_valid = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_age_1",
        bookmaker_id="bet365",
        odds_draw=3.40,
        provider_quote_timestamp_utc=now - timedelta(seconds=899),
        as_of_utc=now,
    )
    assert q_valid.payload["is_executable_price"] is True
    assert q_valid.payload["failure_reason"] is None
    assert q_valid.payload["quote_age_seconds"] == 899.0

    # 2. age == threshold (900.0s) -> valid
    q_boundary = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_age_2",
        bookmaker_id="bet365",
        odds_draw=3.40,
        provider_quote_timestamp_utc=now - timedelta(seconds=900),
        as_of_utc=now,
    )
    assert q_boundary.payload["is_executable_price"] is True
    assert q_boundary.payload["failure_reason"] is None
    assert q_boundary.payload["quote_age_seconds"] == 900.0

    # 3. age > threshold (900.1s) -> stale
    q_stale = daemon.capture_execution_quote(
        internal_fixture_id="fix_E0_age_3",
        bookmaker_id="bet365",
        odds_draw=3.40,
        provider_quote_timestamp_utc=now - timedelta(seconds=901),
        as_of_utc=now,
    )
    assert q_stale.payload["is_executable_price"] is False
    assert q_stale.payload["failure_reason"] == ExecutionFailureReason.EXECUTION_QUOTE_STALE.value
    assert q_stale.payload["quote_age_seconds"] == 901.0
