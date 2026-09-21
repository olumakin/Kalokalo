"""
Market Benchmark Capture Worker (Stage 3).

Captures contemporaneous market benchmark odds snapshots, computes derived
de-vigged market probabilities, and persists immutable evidence records into
the Stage 1 event store strictly bound to Stage 2 internal fixture identity.
Consensus benchmark odds are reference evidence only and NEVER marked executable.
Explicitly separates provider quote timestamp from ingestion timestamp and
preserves sufficient raw and derived market evidence for forensic devig reconstruction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import math
from typing import Any

from src.analytics.devig import devig
from src.tracking.events import (
    EventType,
    ImmutableEventStore,
    ProspectiveEvent,
    TestFlag,
    compute_idempotency_key,
)
from src.validation.manifest import get_code_identity, get_semantic_config_identity
from src.workers import (
    CaptureMode,
    ProspectiveActivationBlockedError,
    ProspectiveActivationContext,
    ProspectiveActivationGuard,
)

logger = logging.getLogger(__name__)


class MarketFailureReason(str, Enum):
    MARKET_SOURCE_UNAVAILABLE = "MARKET_SOURCE_UNAVAILABLE"
    MARKET_PAYLOAD_INVALID = "MARKET_PAYLOAD_INVALID"
    MARKET_FIXTURE_UNRESOLVED = "MARKET_FIXTURE_UNRESOLVED"
    MARKET_TIMESTAMP_INVALID = "MARKET_TIMESTAMP_INVALID"
    MARKET_ODDS_INVALID = "MARKET_ODDS_INVALID"


class MarketBenchmarkDaemon:
    """Consumes market benchmark feeds and captures immutable reference snapshots."""

    def __init__(
        self,
        event_store: ImmutableEventStore | None = None,
        devig_method: str = "shin",
        capture_mode: CaptureMode = CaptureMode.PRE_EPOCH_DRY_RUN,
        epoch_model_sha: str | None = None,
        activation_context: ProspectiveActivationContext | None = None,
    ):
        self.event_store = event_store or ImmutableEventStore()
        self.devig_method = devig_method
        self.capture_mode = capture_mode
        self.epoch_model_sha = epoch_model_sha

        # Invariant: Attempting to emit PROSPECTIVE evidence engages the hardened multi-condition guard
        if self.capture_mode == CaptureMode.PROSPECTIVE:
            ctx = activation_context or ProspectiveActivationContext(
                epoch_state="PRE_OBSERVATION_BLOCKED",
                epoch_model_sha=self.epoch_model_sha,
            )
            ProspectiveActivationGuard.verify(ctx)

    def capture_benchmark_quote(
        self,
        internal_fixture_id: str,
        source: str,
        odds_home: float | None = None,
        odds_draw: float | None = None,
        odds_away: float | None = None,
        provider_event_id: str | None = None,
        provider_quote_timestamp_utc: datetime | None = None,
        actual_kickoff_utc: datetime | None = None,
        as_of_utc: datetime | None = None,
        failure_reason: str | MarketFailureReason | None = None,
    ) -> ProspectiveEvent:
        """Capture immutable benchmark snapshot in Stage 1 event store.

        Explicitly separates provider quote timestamp from ingestion timestamp.
        If provider quote timestamp is unavailable, records status as UNKNOWN and
        does not substitute ingestion timestamp.
        """
        now_utc = as_of_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        # 1. Market timestamp provenance separation (Requirement 7)
        if provider_quote_timestamp_utc is None:
            provider_quote_timestamp_status = "UNKNOWN"
            quote_ts_str: str | None = None
            freshness_evaluable = False
        else:
            if provider_quote_timestamp_utc.tzinfo is None:
                provider_quote_timestamp_utc = provider_quote_timestamp_utc.replace(tzinfo=timezone.utc)

            # Clock anomaly check: quote timestamp cannot be materially in the future (>120s)
            if provider_quote_timestamp_utc > now_utc + timedelta(seconds=120):
                raise ValueError(
                    f"Clock anomaly detected: quote timestamp {provider_quote_timestamp_utc.isoformat()} is materially in "
                    f"the future relative to ingest timestamp {now_utc.isoformat()} (>120s skew)."
                )

            # Pre-match benchmark timing check: quote cannot be taken after a known actual kickoff
            if actual_kickoff_utc is not None:
                ak_dt = actual_kickoff_utc
                if ak_dt.tzinfo is None:
                    ak_dt = ak_dt.replace(tzinfo=timezone.utc)
                if provider_quote_timestamp_utc > ak_dt:
                    raise ValueError(
                        f"Temporal anomaly detected: pre-match benchmark quote timestamp {provider_quote_timestamp_utc.isoformat()} "
                        f"is after known actual kickoff {ak_dt.isoformat()}."
                    )

            provider_quote_timestamp_status = "PRESENT"
            quote_ts_str = provider_quote_timestamp_utc.isoformat()
            freshness_evaluable = True

        ingestion_timestamp_str = now_utc.isoformat()

        # 2. Validate odds values
        effective_failure: str | None = (
            failure_reason.value if isinstance(failure_reason, MarketFailureReason) else failure_reason
        )
        derived_p_home: float | None = None
        derived_p_draw: float | None = None
        derived_p_away: float | None = None

        if effective_failure is None:
            if odds_home is None or odds_draw is None or odds_away is None:
                effective_failure = MarketFailureReason.MARKET_SOURCE_UNAVAILABLE.value
            elif (
                math.isnan(odds_home)
                or math.isnan(odds_draw)
                or math.isnan(odds_away)
                or math.isinf(odds_home)
                or math.isinf(odds_draw)
                or math.isinf(odds_away)
                or odds_home <= 1.0
                or odds_draw <= 1.0
                or odds_away <= 1.0
            ):
                effective_failure = MarketFailureReason.MARKET_ODDS_INVALID.value
            else:
                try:
                    # De-vig raw odds
                    dh, dd, da = devig(
                        float(odds_home),
                        float(odds_draw),
                        float(odds_away),
                        method=self.devig_method,
                    )
                    derived_p_home = float(dh)
                    derived_p_draw = float(dd)
                    derived_p_away = float(da)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Devig calculation failed for %s: %s", internal_fixture_id, exc)
                    effective_failure = MarketFailureReason.MARKET_PAYLOAD_INVALID.value

        # 3. Construct immutable payload preserving raw and derived market data for reconstruction (Requirement 8)
        payload: dict[str, Any] = {
            "internal_fixture_id": internal_fixture_id,
            "source": source,
            "provider_event_id": provider_event_id,
            "provider_quote_timestamp_status": provider_quote_timestamp_status,
            "provider_quote_timestamp_utc": quote_ts_str,
            "ingestion_timestamp_utc": ingestion_timestamp_str,
            "freshness_evaluable": freshness_evaluable,
            "raw_odds_home": float(odds_home) if (odds_home is not None and not math.isnan(odds_home)) else None,
            "raw_odds_draw": float(odds_draw) if (odds_draw is not None and not math.isnan(odds_draw)) else None,
            "raw_odds_away": float(odds_away) if (odds_away is not None and not math.isnan(odds_away)) else None,
            "derived_market_p_home": derived_p_home,
            "derived_market_p_draw": derived_p_draw,
            "derived_market_p_away": derived_p_away,
            "devig_method": self.devig_method,
            "devig_method_version": "devig::v1.0",
            "is_executable_price": False,  # Strict invariant: benchmark only, never executable
            "capture_mode": self.capture_mode.value,
            "prospective_eligible": False,  # Strict invariant for Stage 3
            "evidence_classification": "PRE_EPOCH_VALIDATION",
            "failure_reason": effective_failure,
            "external_contract_identity": "MarketBenchmarkContract::v1.0",
        }

        # 4. Idempotency key binds fixture + quote_ts/ingest_ts + source + odds
        ts_for_key = quote_ts_str if quote_ts_str is not None else f"INGEST_{ingestion_timestamp_str}"
        qualifier = f"{source}:{odds_home}:{odds_draw}:{odds_away}:{provider_quote_timestamp_status}:{effective_failure or 'OK'}"
        ikey = compute_idempotency_key(
            event_type=EventType.MARKET_BENCHMARK_SNAPSHOT,
            entity_id=internal_fixture_id,
            timestamp_str=ts_for_key,
            provider_event_id=provider_event_id,
            qualifier=qualifier,
        )

        code_sha = get_code_identity()
        config_sha = get_semantic_config_identity()

        event = ProspectiveEvent(
            event_type=EventType.MARKET_BENCHMARK_SNAPSHOT,
            epoch_id=self.epoch_model_sha if self.capture_mode == CaptureMode.PROSPECTIVE else "PRE_EPOCH_STAGE_3",
            idempotency_key=ikey,
            created_at_utc=now_utc,
            test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
            code_identity=code_sha,
            semantic_config_identity=config_sha,
            payload=payload,
        )

        saved, _ = self.event_store.append(event)
        return saved


def join_forecast_and_benchmark(
    event_store: ImmutableEventStore,
    internal_fixture_id: str,
) -> dict[str, Any]:
    """Join forecast prediction records and contemporaneous market benchmarks exclusively by internal_fixture_id.

    Rejects composite labels, display team names, and mutable date strings as join keys.
    """
    events = [
        e for e in event_store._events_by_id.values()
        if e.payload.get("internal_fixture_id") == internal_fixture_id
    ]
    prediction_types = {
        EventType.PREDICTION_RECORD,
        EventType.PREDICTION_CAPTURE_SUCCESS,
        EventType.PREDICTION_CAPTURE_FAILURE,
    }
    predictions = [e for e in events if e.event_type in prediction_types]
    benchmarks = [e for e in events if e.event_type == EventType.MARKET_BENCHMARK_SNAPSHOT]

    return {
        "internal_fixture_id": internal_fixture_id,
        "predictions": sorted(predictions, key=lambda e: e.created_at_utc),
        "benchmarks": sorted(benchmarks, key=lambda e: e.created_at_utc),
    }
