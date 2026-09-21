"""
Immutable Prospective Event Storage Engine and Schema (Stage 1).

Defines append-only event models, deterministic idempotency calculation,
and storage invariants for prospective validation without in-place mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator


class EventType(str, Enum):
    FIXTURE_LIFECYCLE = "fixture_lifecycle"
    PREDICTION_RECORD = "prediction_record"
    PREDICTION_CAPTURE_SUCCESS = "prediction_capture_success"
    PREDICTION_CAPTURE_FAILURE = "prediction_capture_failure"
    MARKET_BENCHMARK_SNAPSHOT = "market_benchmark_snapshot"
    EXECUTION_QUOTE_SNAPSHOT = "execution_quote_snapshot"
    DECISION_RECORD = "decision_record"
    CLOSING_LINE_SNAPSHOT = "closing_line_snapshot"
    SETTLEMENT_RECORD = "settlement_record"
    AMENDMENT_EVENT = "amendment_event"
    INTEGRITY_INCIDENT = "integrity_incident"


class TestFlag(str, Enum):
    __test__ = False
    TEST_ONLY_NON_PROSPECTIVE = "TEST_ONLY_NON_PROSPECTIVE"
    PROSPECTIVE = "PROSPECTIVE"


def compute_idempotency_key(
    event_type: str | EventType,
    entity_id: str,
    timestamp_str: str,
    provider_event_id: str | None = None,
    qualifier: str | None = None,
) -> str:
    """Generate a deterministic 64-character SHA-256 idempotency key for an event.

    Binds event type, primary entity (e.g. fixture_id), canonical timestamp,
    optional provider event ID, and qualifier (e.g. bookmaker or scoreline).
    """
    etype = event_type.value if isinstance(event_type, EventType) else str(event_type)
    components = [
        ("entity_id", str(entity_id)),
        ("event_type", etype),
        ("provider_event_id", str(provider_event_id or "")),
        ("qualifier", str(qualifier or "")),
        ("timestamp", str(timestamp_str)),
    ]
    # Canonical string representation
    canonical_str = "|".join(f"{k}:{v}" for k, v in components)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


class ProspectiveEvent(BaseModel):
    """Base immutable record for any prospective validation event."""

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    event_type: EventType
    epoch_id: str | None = None
    idempotency_key: str
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    test_flag: TestFlag = TestFlag.PROSPECTIVE
    target_event_id: str | None = None  # Populated for amendments
    code_identity: str | None = None
    semantic_config_identity: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str) -> str:
        if len(v) != 64:
            raise ValueError(f"idempotency_key must be 64-character hex string, got {len(v)}")
        return v

    @model_validator(mode="after")
    def validate_epoch_binding(self) -> ProspectiveEvent:
        if self.test_flag == TestFlag.PROSPECTIVE and not self.epoch_id:
            raise ValueError("Prospective events (PROSPECTIVE) require an explicit epoch_id")
        return self

    def canonical_json(self) -> str:
        data = self.model_dump(mode="json")
        return json.dumps(data, sort_keys=True, separators=(",", ":"))


class DuplicateIdempotencyCollisionError(Exception):
    """Raised when an identical idempotency key is submitted with conflicting payload."""
    pass


class ImmutableEventStore:
    """In-memory append-only event store enforcing idempotency and no in-place mutation."""

    def __init__(self):
        self._events_by_id: dict[str, ProspectiveEvent] = {}
        self._events_by_key: dict[str, ProspectiveEvent] = {}

    def append(self, event: ProspectiveEvent) -> tuple[ProspectiveEvent, bool]:
        """Append an event to the store.

        Returns (stored_event, inserted_flag).
        If idempotency_key matches an existing event with identical payload,
        returns existing event with inserted_flag=False (idempotent no-op).
        If payload differs, raises DuplicateIdempotencyCollisionError.
        """
        existing = self._events_by_key.get(event.idempotency_key)
        if existing is not None:
            # Check payload equivalence
            if existing.payload == event.payload and existing.event_type == event.event_type:
                return existing, False
            raise DuplicateIdempotencyCollisionError(
                f"Conflicting payload for existing idempotency_key: {event.idempotency_key}"
            )

        self._events_by_id[event.event_id] = event
        self._events_by_key[event.idempotency_key] = event
        return event, True

    def get_by_id(self, event_id: str) -> ProspectiveEvent | None:
        return self._events_by_id.get(event_id)

    def get_by_key(self, idempotency_key: str) -> ProspectiveEvent | None:
        return self._events_by_key.get(idempotency_key)

    def list_prospective_events(self, epoch_id: str | None = None) -> list[ProspectiveEvent]:
        """Return strictly non-test prospective events. Test fixtures are completely excluded."""
        events = [
            e for e in self._events_by_id.values()
            if e.test_flag == TestFlag.PROSPECTIVE
        ]
        if epoch_id is not None:
            events = [e for e in events if e.epoch_id == epoch_id]
        return sorted(events, key=lambda e: e.created_at_utc)

    def append_amendment(
        self,
        target_event_id: str,
        amendment_type: str,
        reason: str,
        corrected_payload: dict[str, Any],
        epoch_id: str | None = None,
        test_flag: TestFlag = TestFlag.PROSPECTIVE,
    ) -> ProspectiveEvent:
        """Append an amendment record preserving the target event unmodified."""
        target = self.get_by_id(target_event_id)
        if target is None:
            raise KeyError(f"Target event {target_event_id} not found")

        now_str = datetime.now(timezone.utc).isoformat()
        ikey = compute_idempotency_key(
            event_type=EventType.AMENDMENT_EVENT,
            entity_id=target_event_id,
            timestamp_str=now_str,
            qualifier=amendment_type,
        )

        amendment = ProspectiveEvent(
            event_type=EventType.AMENDMENT_EVENT,
            epoch_id=epoch_id or target.epoch_id,
            idempotency_key=ikey,
            test_flag=test_flag,
            target_event_id=target_event_id,
            payload={
                "amendment_type": amendment_type,
                "reason": reason,
                "corrected_payload": corrected_payload,
                "original_event_id": target_event_id,
            },
        )
        saved, _ = self.append(amendment)
        return saved
