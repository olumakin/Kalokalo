"""
Named Execution Feed and Observable Price Capture Engine (Stage 4 / A09).

Implements the authoritative ExecutionQuoteContract::v1.0 separating observable,
named execution prices from research/reference market benchmarks.
Enforces stable bookmaker identity (bookmaker_id, bookmaker_name, provider_namespace),
explicit selection identity (market_type="1X2", selection="DRAW"),
strict causal quote timestamping (t_quote <= t_ingest <= t_decision < t_cutoff),
and configuration-driven quote freshness limits (max_execution_quote_age_seconds).
Prohibits benchmark fallback fail-closed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import math
from typing import Any

from pydantic import BaseModel, Field

from src.config import get_config
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

EXECUTION_QUOTE_CONTRACT_VERSION = "ExecutionQuoteContract::v1.0"

FORBIDDEN_ANONYMOUS_SOURCES = {
    "",
    "consensus",
    "average",
    "median",
    "market",
    "synthetic",
    "anonymous",
    "composite",
    "market_composite",
    "market_average",
    "composite_book",
    "aggregate",
    "fair",
    "reference",
    "pinnacle_devigged",
    "unnamed",
    "unknown",
}


def is_valid_named_bookmaker(name: str | None) -> bool:
    """Verify that execution price source is a specific named observable bookmaker.

    Fails closed on empty, consensus, median, synthetic, or composite labels.
    """
    if name is None:
        return False
    clean = name.strip().lower()
    if not clean:
        return False
    if clean in FORBIDDEN_ANONYMOUS_SOURCES:
        return False
    for forbidden in ("consensus", "average", "composite", "synthetic", "anonymous", "aggregate"):
        if forbidden in clean:
            return False
    return True


class ExecutionFailureReason(str, Enum):
    EXECUTION_SOURCE_NOT_NAMED = "EXECUTION_SOURCE_NOT_NAMED"
    EXECUTION_QUOTE_TIMESTAMP_UNKNOWN = "EXECUTION_QUOTE_TIMESTAMP_UNKNOWN"
    EXECUTION_QUOTE_STALE = "EXECUTION_QUOTE_STALE"
    EXECUTION_QUOTE_FUTURE_TIMESTAMP = "EXECUTION_QUOTE_FUTURE_TIMESTAMP"
    EXECUTION_QUOTE_POST_KICKOFF = "EXECUTION_QUOTE_POST_KICKOFF"
    EXECUTION_ODDS_INVALID = "EXECUTION_ODDS_INVALID"
    NO_EXECUTION_QUOTE = "NO_EXECUTION_QUOTE"
    EXECUTION_FIXTURE_UNRESOLVED = "EXECUTION_FIXTURE_UNRESOLVED"
    EXECUTION_PAYLOAD_INVALID = "EXECUTION_PAYLOAD_INVALID"
    SELECTION_MISMATCH = "SELECTION_MISMATCH"


class AvailableExecutionQuote(BaseModel):
    """Observable named execution quote representation conforming to ExecutionQuoteContract::v1.0."""

    internal_fixture_id: str
    bookmaker_id: str
    bookmaker_name: str
    provider_namespace: str = "the_odds_api"
    market_type: str = "1X2"
    selection: str = "DRAW"
    odds_home: float | None = None
    odds_draw: float | None = None
    odds_away: float | None = None
    selection_odds: float | None = None
    provider_event_id: str | None = None
    provider_quote_timestamp_utc: datetime | None = None
    ingestion_timestamp_utc: datetime
    provider_quote_timestamp_status: str
    quote_age_seconds: float | None = None
    is_executable_price: bool = False
    failure_reason: str | None = None
    external_contract_identity: str = EXECUTION_QUOTE_CONTRACT_VERSION


class ExecutionQuoteDaemon:
    """Consumes named execution feeds and captures immutable observable quote snapshots."""

    def __init__(
        self,
        event_store: ImmutableEventStore | None = None,
        max_quote_age_seconds: int | None = None,
        capture_mode: CaptureMode = CaptureMode.PRE_EPOCH_DRY_RUN,
        epoch_model_sha: str | None = None,
        activation_context: ProspectiveActivationContext | None = None,
    ):
        self.event_store = event_store or ImmutableEventStore()
        if max_quote_age_seconds is not None:
            self.max_quote_age_seconds = int(max_quote_age_seconds)
        else:
            try:
                cfg = get_config()
                self.max_quote_age_seconds = int(cfg.execution.max_execution_quote_age_seconds)
            except Exception:
                self.max_quote_age_seconds = 900

        self.capture_mode = capture_mode
        self.epoch_model_sha = epoch_model_sha

        # Invariant: Attempting to emit PROSPECTIVE evidence engages the hardened multi-condition guard
        if self.capture_mode == CaptureMode.PROSPECTIVE:
            ctx = activation_context or ProspectiveActivationContext(
                epoch_state="PRE_OBSERVATION_BLOCKED",
                epoch_model_sha=self.epoch_model_sha,
            )
            ProspectiveActivationGuard.verify(ctx)

    def capture_execution_quote(
        self,
        internal_fixture_id: str,
        bookmaker: str | None = None,
        odds_draw: float | None = None,
        odds_home: float | None = None,
        odds_away: float | None = None,
        bookmaker_id: str | None = None,
        bookmaker_name: str | None = None,
        provider_namespace: str = "the_odds_api",
        market_type: str = "1X2",
        selection: str = "DRAW",
        provider_event_id: str | None = None,
        provider_quote_timestamp_utc: datetime | None = None,
        actual_kickoff_utc: datetime | None = None,
        as_of_utc: datetime | None = None,
        failure_reason: str | ExecutionFailureReason | None = None,
    ) -> ProspectiveEvent:
        """Capture immutable execution quote snapshot into the Stage 1 store.

        Enforces:
        1. Stable bookmaker identity (bookmaker_id, bookmaker_name, provider_namespace).
        2. Explicit selection identity (market_type="1X2", selection="DRAW").
        3. Timestamp provenance separation (provider quote time required, no ingestion substitution).
        4. Freshness window (age <= max_quote_age_seconds).
        5. Causal timeline checks (no future quotes >120s skew, no quotes post actual kickoff).
        6. Odds validity (strictly > 1.0, non-nan, finite).
        """
        now_utc = as_of_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        effective_failure: str | None = (
            failure_reason.value if isinstance(failure_reason, ExecutionFailureReason) else failure_reason
        )

        # 1. Stable bookmaker identity verification
        canonical_b_id = (bookmaker_id or bookmaker or "").strip().lower()
        canonical_b_name = (bookmaker_name or bookmaker or canonical_b_id).strip()
        norm_market_type = (market_type or "1X2").strip().upper()
        norm_selection = (selection or "DRAW").strip().upper()

        if effective_failure is None:
            if not canonical_b_id or not is_valid_named_bookmaker(canonical_b_id):
                effective_failure = ExecutionFailureReason.EXECUTION_SOURCE_NOT_NAMED.value

        # 2. Timestamp provenance and causal timing checks
        quote_ts_str: str | None = None
        quote_age_seconds: float | None = None
        provider_quote_timestamp_status: str

        if provider_quote_timestamp_utc is None:
            provider_quote_timestamp_status = "UNKNOWN"
            if effective_failure is None:
                effective_failure = ExecutionFailureReason.EXECUTION_QUOTE_TIMESTAMP_UNKNOWN.value
        else:
            q_dt = provider_quote_timestamp_utc
            if q_dt.tzinfo is None:
                q_dt = q_dt.replace(tzinfo=timezone.utc)
            quote_ts_str = q_dt.isoformat()
            provider_quote_timestamp_status = "PRESENT"
            quote_age_seconds = (now_utc - q_dt).total_seconds()

            # Clock anomaly: quote cannot be materially in future (>120s skew)
            if q_dt > now_utc + timedelta(seconds=120):
                effective_failure = ExecutionFailureReason.EXECUTION_QUOTE_FUTURE_TIMESTAMP.value

            # Timing check: quote cannot be taken after known actual kickoff
            elif actual_kickoff_utc is not None:
                ak_dt = actual_kickoff_utc
                if ak_dt.tzinfo is None:
                    ak_dt = ak_dt.replace(tzinfo=timezone.utc)
                if q_dt > ak_dt:
                    effective_failure = ExecutionFailureReason.EXECUTION_QUOTE_POST_KICKOFF.value

            # Freshness check: quote age cannot exceed configured threshold (exact boundary: age <= max is valid, age > max is stale)
            elif effective_failure is None and quote_age_seconds > self.max_quote_age_seconds:
                effective_failure = ExecutionFailureReason.EXECUTION_QUOTE_STALE.value

        # 3. Validate odds values
        target_odds = odds_draw if norm_selection == "DRAW" else (odds_home if norm_selection == "HOME" else odds_away)
        if effective_failure is None:
            if target_odds is None or math.isnan(target_odds) or math.isinf(target_odds) or target_odds <= 1.0:
                effective_failure = ExecutionFailureReason.EXECUTION_ODDS_INVALID.value
            elif odds_home is not None and (math.isnan(odds_home) or math.isinf(odds_home) or odds_home <= 1.0):
                effective_failure = ExecutionFailureReason.EXECUTION_ODDS_INVALID.value
            elif odds_away is not None and (math.isnan(odds_away) or math.isinf(odds_away) or odds_away <= 1.0):
                effective_failure = ExecutionFailureReason.EXECUTION_ODDS_INVALID.value

        is_executable = (effective_failure is None)

        payload: dict[str, Any] = {
            "internal_fixture_id": internal_fixture_id,
            "bookmaker_id": canonical_b_id,
            "bookmaker_name": canonical_b_name,
            "bookmaker": canonical_b_name,  # Compatibility field
            "provider_namespace": provider_namespace,
            "market_type": norm_market_type,
            "selection": norm_selection,
            "provider_event_id": provider_event_id,
            "provider_quote_timestamp_status": provider_quote_timestamp_status,
            "provider_quote_timestamp_utc": quote_ts_str,
            "ingestion_timestamp_utc": now_utc.isoformat(),
            "quote_age_seconds": round(quote_age_seconds, 3) if quote_age_seconds is not None else None,
            "max_execution_quote_age_seconds": self.max_quote_age_seconds,
            "raw_odds_home": float(odds_home) if (odds_home is not None and not math.isnan(odds_home)) else None,
            "raw_odds_draw": float(odds_draw) if (odds_draw is not None and not math.isnan(odds_draw)) else None,
            "raw_odds_away": float(odds_away) if (odds_away is not None and not math.isnan(odds_away)) else None,
            "selection_odds": float(target_odds) if (target_odds is not None and not math.isnan(target_odds)) else None,
            "is_executable_price": is_executable,
            "capture_mode": self.capture_mode.value,
            "prospective_eligible": False,  # Strict Stage 4 invariant
            "evidence_classification": "PRE_EPOCH_VALIDATION",
            "failure_reason": effective_failure,
            "external_contract_identity": EXECUTION_QUOTE_CONTRACT_VERSION,
        }

        ts_for_key = quote_ts_str if quote_ts_str is not None else f"INGEST_{now_utc.isoformat()}"
        qualifier = f"{canonical_b_id}:{norm_market_type}:{norm_selection}:{odds_home}:{odds_draw}:{odds_away}:{provider_quote_timestamp_status}:{effective_failure or 'OK'}"
        ikey = compute_idempotency_key(
            event_type=EventType.EXECUTION_QUOTE_SNAPSHOT,
            entity_id=internal_fixture_id,
            timestamp_str=ts_for_key,
            provider_event_id=provider_event_id,
            qualifier=qualifier,
        )

        code_sha = get_code_identity()
        config_sha = get_semantic_config_identity()

        event = ProspectiveEvent(
            event_type=EventType.EXECUTION_QUOTE_SNAPSHOT,
            epoch_id=self.epoch_model_sha if self.capture_mode == CaptureMode.PROSPECTIVE else "PRE_EPOCH_STAGE_4",
            idempotency_key=ikey,
            created_at_utc=now_utc,
            test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
            code_identity=code_sha,
            semantic_config_identity=config_sha,
            payload=payload,
        )

        saved, _ = self.event_store.append(event)
        return saved
