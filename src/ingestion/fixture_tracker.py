"""
Authoritative fixture identity registry and event-driven lifecycle engine (Stage 2).

Manages internal canonical fixture identities, provider aliases, and immutable
lifecycle transitions without destructive overwrites or synthetic mutable string IDs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any
import uuid

from pydantic import BaseModel, Field, field_validator

from src.tracking.events import (
    EventType,
    ImmutableEventStore,
    ProspectiveEvent,
    TestFlag,
    compute_idempotency_key,
)
from src.validation.manifest import EXTERNAL_CONTRACT_IDENTITIES, get_code_identity, get_semantic_config_identity


class FixtureLifecycleState(str, Enum):
    SCHEDULED = "SCHEDULED"
    POSTPONED = "POSTPONED"
    RESCHEDULED = "RESCHEDULED"
    STARTED = "STARTED"
    ABANDONED_PENDING_RULING = "ABANDONED_PENDING_RULING"
    COMPLETED = "COMPLETED"
    OFFICIAL_RESULT_STANDS = "OFFICIAL_RESULT_STANDS"
    VOIDED = "VOIDED"


LEGAL_TRANSITIONS: dict[FixtureLifecycleState, set[FixtureLifecycleState]] = {
    FixtureLifecycleState.SCHEDULED: {
        FixtureLifecycleState.POSTPONED,
        FixtureLifecycleState.RESCHEDULED,
        FixtureLifecycleState.STARTED,
        FixtureLifecycleState.VOIDED,
    },
    FixtureLifecycleState.POSTPONED: {
        FixtureLifecycleState.RESCHEDULED,
        FixtureLifecycleState.VOIDED,
    },
    FixtureLifecycleState.RESCHEDULED: {
        FixtureLifecycleState.SCHEDULED,
        FixtureLifecycleState.STARTED,
        FixtureLifecycleState.POSTPONED,
        FixtureLifecycleState.VOIDED,
    },
    FixtureLifecycleState.STARTED: {
        FixtureLifecycleState.COMPLETED,
        FixtureLifecycleState.ABANDONED_PENDING_RULING,
        FixtureLifecycleState.VOIDED,
    },
    FixtureLifecycleState.ABANDONED_PENDING_RULING: {
        FixtureLifecycleState.OFFICIAL_RESULT_STANDS,
        FixtureLifecycleState.VOIDED,
    },
    FixtureLifecycleState.COMPLETED: {
        FixtureLifecycleState.OFFICIAL_RESULT_STANDS,
        FixtureLifecycleState.VOIDED,
    },
    FixtureLifecycleState.OFFICIAL_RESULT_STANDS: set(),  # Terminal state
    FixtureLifecycleState.VOIDED: set(),                 # Terminal state
}


class ActualKickoffStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    OFFICIALLY_VERIFIED = "OFFICIALLY_VERIFIED"


class IngestDisposition(str, Enum):
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    VALID_LIFECYCLE_UPDATE = "VALID_LIFECYCLE_UPDATE"
    PROVIDER_CORRECTION = "PROVIDER_CORRECTION"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class IllegalStateTransitionError(ValueError):
    """Raised when an illegal lifecycle state transition is attempted without amendment."""
    pass


class IdentityConflictError(ValueError):
    """Raised when incoming provider data conflicts with established fixture identity."""
    pass


class ReconciliationRequiredError(RuntimeError):
    """Raised when alias linkage or fixture identity cannot be unambiguously reconciled."""

    def __init__(
        self,
        message: str,
        disposition: IngestDisposition = IngestDisposition.RECONCILIATION_REQUIRED,
    ):
        super().__init__(message)
        self.disposition = disposition


def generate_internal_fixture_id(
    competition_id: str,
    season_id: str,
    home_team_id: str,
    away_team_id: str,
    occurrence_index: int = 1,
) -> str:
    """Generate deterministic internal Kalokalo fixture ID independent of mutable timestamps.

    Natural keys (competition, season, home, away) remain reconciliation evidence.
    The authoritative registry guarantees uniqueness by binding a monotonic occurrence index.
    Survives kickoff-time changes, date shifts, and provider ID replacements.
    """
    comp = competition_id.strip().upper()
    season = season_id.strip()
    home = home_team_id.strip().upper()
    away = away_team_id.strip().upper()
    occ = int(occurrence_index)

    if not comp or not season or not home or not away:
        raise IdentityConflictError("Natural key components cannot be empty")
    if home == away:
        raise IdentityConflictError(f"Self-play prohibited: home and away identical ({home})")
    if occ < 1:
        raise ValueError(f"occurrence_index must be >= 1, got {occ}")

    raw_key = f"{comp}:{season}:{home}:{away}:{occ:02d}"
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
    return f"fix_{comp.lower()}_{digest}"


class ScheduleChangeRecord(BaseModel):
    timestamp_utc: datetime
    previous_kickoff_utc: datetime
    new_kickoff_utc: datetime
    reason: str | None = None
    provider_namespace: str | None = None


class FixtureStateProjection(BaseModel):
    """Derived current-state projection reproducible from immutable lifecycle events."""

    internal_fixture_id: str
    competition_id: str
    season_id: str
    home_team_id: str
    away_team_id: str
    home_display_name: str
    away_display_name: str
    current_state: FixtureLifecycleState
    original_scheduled_kickoff_utc: datetime
    latest_scheduled_kickoff_utc: datetime
    actual_kickoff_utc: datetime | None = None
    actual_kickoff_status: ActualKickoffStatus = ActualKickoffStatus.UNKNOWN
    schedule_history: list[ScheduleChangeRecord] = Field(default_factory=list)
    provider_aliases: dict[str, list[str]] = Field(default_factory=dict)  # namespace -> list of event_ids
    event_ids: list[str] = Field(default_factory=list)


class IngestResult(BaseModel):
    disposition: IngestDisposition
    internal_fixture_id: str
    event_id: str | None = None
    message: str = ""


class FixtureTracker:
    """Authoritative registry managing fixture identity and lifecycle event processing."""

    def __init__(self, event_store: ImmutableEventStore | None = None):
        self.event_store = event_store or ImmutableEventStore()
        # provider_namespace:provider_event_id -> internal_fixture_id
        self._provider_alias_index: dict[str, str] = {}
        # internal_fixture_id -> FixtureStateProjection
        self._projections: dict[str, FixtureStateProjection] = {}
        # (competition_id, season_id, home_team_id, away_team_id) -> list[internal_fixture_id]
        self._fixtures_by_natural_key: dict[tuple[str, str, str, str], list[str]] = {}

    def get_projection(self, internal_fixture_id: str) -> FixtureStateProjection | None:
        return self._projections.get(internal_fixture_id)

    def get_events_for_fixture(self, internal_fixture_id: str) -> list[ProspectiveEvent]:
        """Retrieve all lifecycle events for a fixture from the authoritative Stage 1 event store."""
        events = [
            e for e in self.event_store._events_by_id.values()
            if e.payload.get("internal_fixture_id") == internal_fixture_id
        ]
        return sorted(events, key=lambda e: (e.payload.get("fixture_event_sequence", 0), e.created_at_utc))

    def resolve_by_provider_id(self, provider_namespace: str, provider_event_id: str) -> str | None:
        key = f"{provider_namespace}:{provider_event_id}"
        return self._provider_alias_index.get(key)

    def process_provider_event(
        self,
        provider_namespace: str,
        provider_event_id: str,
        competition_id: str,
        season_id: str,
        home_team_id: str,
        away_team_id: str,
        scheduled_kickoff_utc: datetime,
        reported_state: FixtureLifecycleState = FixtureLifecycleState.SCHEDULED,
        home_display_name: str | None = None,
        away_display_name: str | None = None,
        actual_kickoff_utc: datetime | None = None,
        actual_kickoff_status: ActualKickoffStatus = ActualKickoffStatus.UNKNOWN,
        provider_event_timestamp_utc: datetime | None = None,
        reason: str | None = None,
        allow_alias_linkage: bool = True,
        create_new_fixture: bool = False,
        test_flag: TestFlag = TestFlag.TEST_ONLY_NON_PROSPECTIVE,
    ) -> IngestResult:
        """Process an incoming provider fixture observation with fail-closed reconciliation."""
        comp = competition_id.strip().upper()
        season = season_id.strip()
        home = home_team_id.strip().upper()
        away = away_team_id.strip().upper()

        if not comp or not season or not home or not away:
            raise IdentityConflictError("Natural key components cannot be empty")
        if home == away:
            raise IdentityConflictError(f"Self-play prohibited: home_team_id and away_team_id are identical ({home})")

        # Kickoff status invariants
        if actual_kickoff_status == ActualKickoffStatus.UNKNOWN and actual_kickoff_utc is not None:
            raise ValueError("actual_kickoff_utc cannot be set when actual_kickoff_status is UNKNOWN")
        if actual_kickoff_status != ActualKickoffStatus.UNKNOWN and actual_kickoff_utc is None:
            raise ValueError(f"actual_kickoff_status is {actual_kickoff_status.value} but actual_kickoff_utc is None")

        provider_alias_key = f"{provider_namespace}:{provider_event_id}"
        existing_internal_id = self._provider_alias_index.get(provider_alias_key)

        # 1. Check existing alias index
        if existing_internal_id is not None:
            projection = self._projections[existing_internal_id]

            # Verify competition and team identity match exactly
            if (
                projection.competition_id != comp
                or projection.home_team_id != home
                or projection.away_team_id != away
            ):
                raise IdentityConflictError(
                    f"Provider event ID {provider_event_id} in namespace {provider_namespace} already bound to "
                    f"{projection.competition_id} {projection.home_team_id} vs {projection.away_team_id}; "
                    f"conflicts with incoming {comp} {home} vs {away} (swapped or reused ID)."
                )

            # Check terminal absorbing states: cannot transition out
            if projection.current_state in {FixtureLifecycleState.OFFICIAL_RESULT_STANDS, FixtureLifecycleState.VOIDED}:
                if reported_state != projection.current_state:
                    raise IllegalStateTransitionError(
                        f"Fixture {existing_internal_id} is in terminal absorbing state {projection.current_state.value}; "
                        f"cannot transition to {reported_state.value} (stale pre-terminal or invalid event)."
                    )
                return IngestResult(
                    disposition=IngestDisposition.IDEMPOTENT_REPLAY,
                    internal_fixture_id=existing_internal_id,
                    message="Terminal state replayed idempotently",
                )

            # Check for exact duplicate observation (idempotent replay)
            if (
                projection.current_state == reported_state
                and projection.latest_scheduled_kickoff_utc == scheduled_kickoff_utc
                and projection.actual_kickoff_utc == actual_kickoff_utc
                and projection.actual_kickoff_status == actual_kickoff_status
            ):
                return IngestResult(
                    disposition=IngestDisposition.IDEMPOTENT_REPLAY,
                    internal_fixture_id=existing_internal_id,
                    message="Identical observation replayed without modification",
                )

            # Check for schedule change / correction
            kickoff_changed = projection.latest_scheduled_kickoff_utc != scheduled_kickoff_utc
            state_changed = projection.current_state != reported_state

            if state_changed:
                self._validate_state_transition(projection.current_state, reported_state)

            disposition = (
                IngestDisposition.VALID_LIFECYCLE_UPDATE
                if state_changed
                else IngestDisposition.PROVIDER_CORRECTION
            )

            event = self._emit_lifecycle_event(
                internal_fixture_id=existing_internal_id,
                competition_id=comp,
                season_id=season,
                home_team_id=home,
                away_team_id=away,
                home_display_name=home_display_name or projection.home_display_name,
                away_display_name=away_display_name or projection.away_display_name,
                previous_state=projection.current_state,
                new_state=reported_state,
                scheduled_kickoff_utc=scheduled_kickoff_utc,
                actual_kickoff_utc=actual_kickoff_utc,
                actual_kickoff_status=actual_kickoff_status,
                provider_namespace=provider_namespace,
                provider_event_id=provider_event_id,
                provider_event_timestamp_utc=provider_event_timestamp_utc,
                reason=reason,
                test_flag=test_flag,
            )

            # Apply to projection
            self._apply_event_to_projection(projection, event, kickoff_changed)

            return IngestResult(
                disposition=disposition,
                internal_fixture_id=existing_internal_id,
                event_id=event.event_id,
                message=f"Updated fixture state to {reported_state.value}",
            )

        # 2. Provider event ID is unseen. Inspect known candidate fixtures for this natural key
        nat_key = (comp, season, home, away)
        candidate_ids = self._fixtures_by_natural_key.get(nat_key, [])

        if not create_new_fixture and len(candidate_ids) > 0:
            # Partition candidates into active vs terminal
            active_candidates = [
                fid for fid in candidate_ids
                if self._projections[fid].current_state not in {
                    FixtureLifecycleState.OFFICIAL_RESULT_STANDS,
                    FixtureLifecycleState.VOIDED,
                }
            ]

            if len(active_candidates) == 1:
                if not allow_alias_linkage:
                    raise ReconciliationRequiredError(
                        f"Unseen provider event {provider_event_id} matches existing active fixture {active_candidates[0]} "
                        f"but automatic alias linkage is disabled",
                        disposition=IngestDisposition.RECONCILIATION_REQUIRED,
                    )

                candidate_internal_id = active_candidates[0]
                existing_fixture = self._projections[candidate_internal_id]

                # Bind alias
                self._provider_alias_index[provider_alias_key] = candidate_internal_id
                if provider_namespace not in existing_fixture.provider_aliases:
                    existing_fixture.provider_aliases[provider_namespace] = []
                if provider_event_id not in existing_fixture.provider_aliases[provider_namespace]:
                    existing_fixture.provider_aliases[provider_namespace].append(provider_event_id)

                kickoff_changed = existing_fixture.latest_scheduled_kickoff_utc != scheduled_kickoff_utc
                state_changed = existing_fixture.current_state != reported_state

                if state_changed:
                    self._validate_state_transition(existing_fixture.current_state, reported_state)

                event = self._emit_lifecycle_event(
                    internal_fixture_id=candidate_internal_id,
                    competition_id=comp,
                    season_id=season,
                    home_team_id=home,
                    away_team_id=away,
                    home_display_name=home_display_name or existing_fixture.home_display_name,
                    away_display_name=away_display_name or existing_fixture.away_display_name,
                    previous_state=existing_fixture.current_state,
                    new_state=reported_state,
                    scheduled_kickoff_utc=scheduled_kickoff_utc,
                    actual_kickoff_utc=actual_kickoff_utc,
                    actual_kickoff_status=actual_kickoff_status,
                    provider_namespace=provider_namespace,
                    provider_event_id=provider_event_id,
                    provider_event_timestamp_utc=provider_event_timestamp_utc,
                    reason=reason or f"Linked new provider alias {provider_event_id}",
                    test_flag=test_flag,
                )

                self._apply_event_to_projection(existing_fixture, event, kickoff_changed)

                return IngestResult(
                    disposition=IngestDisposition.VALID_LIFECYCLE_UPDATE,
                    internal_fixture_id=candidate_internal_id,
                    event_id=event.event_id,
                    message=f"Linked provider ID {provider_event_id} to existing fixture {candidate_internal_id}",
                )

            elif len(active_candidates) > 1:
                # Ambiguous: multiple active candidate fixtures exist for the same natural key
                raise ReconciliationRequiredError(
                    f"Ambiguous reconciliation: provider event {provider_event_id} matches multiple ({len(active_candidates)}) "
                    f"active candidate fixtures {active_candidates}. Manual reconciliation required.",
                    disposition=IngestDisposition.RECONCILIATION_REQUIRED,
                )
            # If len(active_candidates) == 0: all previous fixtures are terminal (completed/voided).
            # Fall through to initialize the new distinct fixture.

        # 3. New fixture initialization (assigned distinct occurrence index by registry)
        occ_index = len(candidate_ids) + 1
        internal_id = generate_internal_fixture_id(comp, season, home, away, occurrence_index=occ_index)

        self._fixtures_by_natural_key.setdefault(nat_key, []).append(internal_id)
        self._provider_alias_index[provider_alias_key] = internal_id

        event = self._emit_lifecycle_event(
            internal_fixture_id=internal_id,
            competition_id=comp,
            season_id=season,
            home_team_id=home,
            away_team_id=away,
            home_display_name=home_display_name or home,
            away_display_name=away_display_name or away,
            previous_state=None,
            new_state=reported_state,
            scheduled_kickoff_utc=scheduled_kickoff_utc,
            actual_kickoff_utc=actual_kickoff_utc,
            actual_kickoff_status=actual_kickoff_status,
            provider_namespace=provider_namespace,
            provider_event_id=provider_event_id,
            provider_event_timestamp_utc=provider_event_timestamp_utc,
            reason=reason or "Initial schedule observation",
            test_flag=test_flag,
        )

        projection = FixtureStateProjection(
            internal_fixture_id=internal_id,
            competition_id=comp,
            season_id=season,
            home_team_id=home,
            away_team_id=away,
            home_display_name=home_display_name or home,
            away_display_name=away_display_name or away,
            current_state=reported_state,
            original_scheduled_kickoff_utc=scheduled_kickoff_utc,
            latest_scheduled_kickoff_utc=scheduled_kickoff_utc,
            actual_kickoff_utc=actual_kickoff_utc,
            actual_kickoff_status=actual_kickoff_status,
            schedule_history=[],
            provider_aliases={provider_namespace: [provider_event_id]},
            event_ids=[event.event_id],
        )
        self._projections[internal_id] = projection

        return IngestResult(
            disposition=IngestDisposition.VALID_LIFECYCLE_UPDATE,
            internal_fixture_id=internal_id,
            event_id=event.event_id,
            message="Initialized new fixture",
        )

    def _validate_state_transition(self, current: FixtureLifecycleState, new: FixtureLifecycleState) -> None:
        if current == new:
            return
        allowed = LEGAL_TRANSITIONS.get(current, set())
        if new not in allowed:
            raise IllegalStateTransitionError(
                f"Illegal fixture lifecycle transition: {current.value} -> {new.value}. "
                f"Allowed destinations: {[s.value for s in allowed]}"
            )

    def _emit_lifecycle_event(
        self,
        internal_fixture_id: str,
        competition_id: str,
        season_id: str,
        home_team_id: str,
        away_team_id: str,
        home_display_name: str,
        away_display_name: str,
        previous_state: FixtureLifecycleState | None,
        new_state: FixtureLifecycleState,
        scheduled_kickoff_utc: datetime,
        actual_kickoff_utc: datetime | None,
        actual_kickoff_status: ActualKickoffStatus,
        provider_namespace: str,
        provider_event_id: str,
        provider_event_timestamp_utc: datetime | None,
        reason: str | None,
        test_flag: TestFlag,
    ) -> ProspectiveEvent:
        proj = self._projections.get(internal_fixture_id)
        seq = (len(proj.event_ids) + 1) if proj is not None else 1
        now_utc = datetime.now(timezone.utc)
        prov_ts = provider_event_timestamp_utc or now_utc

        payload = {
            "internal_fixture_id": internal_fixture_id,
            "competition_id": competition_id,
            "season_id": season_id,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_display_name": home_display_name,
            "away_display_name": away_display_name,
            "previous_state": previous_state.value if previous_state else None,
            "new_state": new_state.value,
            "scheduled_kickoff_utc": scheduled_kickoff_utc.isoformat(),
            "actual_kickoff_utc": actual_kickoff_utc.isoformat() if actual_kickoff_utc else None,
            "actual_kickoff_status": actual_kickoff_status.value,
            "provider_namespace": provider_namespace,
            "provider_event_id": provider_event_id,
            "provider_event_timestamp_utc": prov_ts.isoformat(),
            "ingestion_timestamp_utc": now_utc.isoformat(),
            "fixture_event_sequence": seq,
            "reason": reason,
        }

        # Idempotency key binds event identity: fixture + new_state + scheduled_kickoff + provider info + sequence
        ikey = compute_idempotency_key(
            event_type=EventType.FIXTURE_LIFECYCLE,
            entity_id=internal_fixture_id,
            timestamp_str=scheduled_kickoff_utc.isoformat(),
            provider_event_id=provider_event_id,
            qualifier=f"{new_state.value}:{actual_kickoff_status.value}:{seq}",
        )

        code_sha = get_code_identity()
        config_sha = get_semantic_config_identity()

        event = ProspectiveEvent(
            event_type=EventType.FIXTURE_LIFECYCLE,
            epoch_id="TEST_EPOCH" if test_flag == TestFlag.TEST_ONLY_NON_PROSPECTIVE else None,
            idempotency_key=ikey,
            created_at_utc=now_utc,
            test_flag=test_flag,
            code_identity=code_sha,
            semantic_config_identity=config_sha,
            payload=payload,
        )

        saved, _ = self.event_store.append(event)
        return saved

    def _apply_event_to_projection(
        self,
        projection: FixtureStateProjection,
        event: ProspectiveEvent,
        kickoff_changed: bool,
    ) -> None:
        p = event.payload
        new_state = FixtureLifecycleState(p["new_state"])
        sched_kickoff = datetime.fromisoformat(p["scheduled_kickoff_utc"])
        act_kickoff = datetime.fromisoformat(p["actual_kickoff_utc"]) if p["actual_kickoff_utc"] else None
        act_status = ActualKickoffStatus(p["actual_kickoff_status"])

        if kickoff_changed:
            change_rec = ScheduleChangeRecord(
                timestamp_utc=event.created_at_utc,
                previous_kickoff_utc=projection.latest_scheduled_kickoff_utc,
                new_kickoff_utc=sched_kickoff,
                reason=p.get("reason"),
                provider_namespace=p.get("provider_namespace"),
            )
            projection.schedule_history.append(change_rec)
            projection.latest_scheduled_kickoff_utc = sched_kickoff

        projection.current_state = new_state
        if act_kickoff is not None:
            projection.actual_kickoff_utc = act_kickoff
            projection.actual_kickoff_status = act_status

        if event.event_id not in projection.event_ids:
            projection.event_ids.append(event.event_id)


def rebuild_projection_from_events(events: list[ProspectiveEvent]) -> FixtureStateProjection:
    """Deterministically reconstruct derived FixtureStateProjection by replaying raw events."""
    if not events:
        raise ValueError("Cannot rebuild projection from empty event list")

    # Order events deterministically:
    # 1. provider_event_timestamp_utc
    # 2. fixture_event_sequence
    # 3. created_at_utc
    # 4. event_id
    def _event_sort_key(e: ProspectiveEvent) -> tuple[datetime, int, datetime, str]:
        p = e.payload
        pts_str = p.get("provider_event_timestamp_utc")
        pts = datetime.fromisoformat(pts_str) if pts_str else e.created_at_utc
        seq = int(p.get("fixture_event_sequence", 0))
        return (pts, seq, e.created_at_utc, e.event_id)

    sorted_events = sorted(events, key=_event_sort_key)

    # Validate single fixture scope
    target_fixture_id = sorted_events[0].payload["internal_fixture_id"]
    for ev in sorted_events:
        if ev.payload.get("internal_fixture_id") != target_fixture_id:
            raise IdentityConflictError(
                f"Cannot rebuild projection across multiple fixtures: "
                f"{target_fixture_id} vs {ev.payload.get('internal_fixture_id')}"
            )

    first_payload = sorted_events[0].payload
    initial_sched = datetime.fromisoformat(first_payload["scheduled_kickoff_utc"])
    initial_act = datetime.fromisoformat(first_payload["actual_kickoff_utc"]) if first_payload.get("actual_kickoff_utc") else None
    initial_status = ActualKickoffStatus(first_payload.get("actual_kickoff_status", "UNKNOWN"))

    projection = FixtureStateProjection(
        internal_fixture_id=first_payload["internal_fixture_id"],
        competition_id=first_payload["competition_id"],
        season_id=first_payload["season_id"],
        home_team_id=first_payload["home_team_id"],
        away_team_id=first_payload["away_team_id"],
        home_display_name=first_payload["home_display_name"],
        away_display_name=first_payload["away_display_name"],
        current_state=FixtureLifecycleState(first_payload["new_state"]),
        original_scheduled_kickoff_utc=initial_sched,
        latest_scheduled_kickoff_utc=initial_sched,
        actual_kickoff_utc=initial_act,
        actual_kickoff_status=initial_status,
        schedule_history=[],
        provider_aliases={},
        event_ids=[],
    )

    ns0 = first_payload.get("provider_namespace")
    pid0 = first_payload.get("provider_event_id")
    if ns0 and pid0:
        projection.provider_aliases[ns0] = [pid0]
    projection.event_ids.append(sorted_events[0].event_id)

    for event in sorted_events[1:]:
        p = event.payload
        ns = p.get("provider_namespace")
        pid = p.get("provider_event_id")
        if ns and pid:
            if ns not in projection.provider_aliases:
                projection.provider_aliases[ns] = []
            if pid not in projection.provider_aliases[ns]:
                projection.provider_aliases[ns].append(pid)

        new_state = FixtureLifecycleState(p["new_state"])
        if new_state != projection.current_state:
            if projection.current_state in {FixtureLifecycleState.OFFICIAL_RESULT_STANDS, FixtureLifecycleState.VOIDED}:
                raise IllegalStateTransitionError(
                    f"Terminal fixture state {projection.current_state.value} cannot transition to {new_state.value} (stale or contradictory event)"
                )
            allowed = LEGAL_TRANSITIONS.get(projection.current_state, set())
            if new_state not in allowed:
                raise IllegalStateTransitionError(
                    f"Illegal fixture lifecycle transition during replay: {projection.current_state.value} -> {new_state.value}. "
                    f"Allowed destinations: {[s.value for s in allowed]}"
                )
            projection.current_state = new_state

        new_sched = datetime.fromisoformat(p["scheduled_kickoff_utc"])
        if new_sched != projection.latest_scheduled_kickoff_utc:
            projection.schedule_history.append(
                ScheduleChangeRecord(
                    timestamp_utc=event.created_at_utc,
                    previous_kickoff_utc=projection.latest_scheduled_kickoff_utc,
                    new_kickoff_utc=new_sched,
                    reason=p.get("reason"),
                    provider_namespace=ns,
                )
            )
            projection.latest_scheduled_kickoff_utc = new_sched

        if p.get("actual_kickoff_utc"):
            projection.actual_kickoff_utc = datetime.fromisoformat(p["actual_kickoff_utc"])
            projection.actual_kickoff_status = ActualKickoffStatus(p.get("actual_kickoff_status", "UNKNOWN"))

        if event.event_id not in projection.event_ids:
            projection.event_ids.append(event.event_id)

    return projection
