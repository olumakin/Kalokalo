"""
Prospective Prediction Capture Worker (Stage 3).

Consumes authoritative Stage 2 fixture projections, executes the frozen
Dixon-Coles domain model before the decision lead-time cutoff, and emits
immutable prediction evidence records into the Stage 1 event store.
Separates successful forecast events from capture failure records, binds
predictions to deterministic training and model fit identities, enforces
anti-look-ahead information cutoff invariants, and gates on fixture lifecycle states.
Operates safely in pre-Epoch dry-run mode with fail-closed prospective gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
from typing import Any

from src.config import AppConfig, get_config
from src.engine import DomainPredictionEngine
from src.ingestion.fixture_tracker import (
    ActualKickoffStatus,
    FixtureLifecycleState,
    FixtureStateProjection,
)
from src.models.dixon_coles import DixonColesModel, UnknownTeamError
from src.models.simulator import match_probabilities
from src.tracking.events import (
    EventType,
    ImmutableEventStore,
    ProspectiveEvent,
    TestFlag,
    compute_idempotency_key,
)
from src.validation.manifest import (
    EXTERNAL_CONTRACT_IDENTITIES,
    get_code_identity,
    get_semantic_config_identity,
)
from src.validation.provenance import compute_model_fit_identity
from src.workers import (
    CaptureMode,
    ProspectiveActivationBlockedError,
    ProspectiveActivationContext,
    ProspectiveActivationGuard,
)

logger = logging.getLogger(__name__)


class ModelFailureReason(str, Enum):
    UNKNOWN_TEAM = "UNKNOWN_TEAM"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MODEL_NON_CONVERGENCE = "MODEL_NON_CONVERGENCE"
    POST_CUTOFF_ATTEMPT = "POST_CUTOFF_ATTEMPT"
    UNMODELED_LEAGUE = "UNMODELED_LEAGUE"
    LIFECYCLE_POSTPONED = "LIFECYCLE_POSTPONED"
    LIFECYCLE_STARTED = "LIFECYCLE_STARTED"
    LIFECYCLE_TERMINAL = "LIFECYCLE_TERMINAL"
    FUTURE_DATA_LEAKAGE = "FUTURE_DATA_LEAKAGE"


@dataclass(frozen=True)
class TimingContract:
    """Configurable prediction timing contract relative to scheduled kickoff."""

    decision_lead_time_seconds: int = 3600  # Default 60 minutes
    timing_contract_state: str = "PRE_EPOCH_PROVISIONAL"
    contract_version: str = "ForecastTimingContract::v1.0"

    def is_before_cutoff(
        self,
        prediction_time_utc: datetime,
        scheduled_kickoff_utc: datetime,
    ) -> bool:
        """Evaluate timing condition: t_pred < t_kickoff_sched - decision_lead_time.

        Predictions at or after the cutoff must be rejected.
        """
        cutoff = scheduled_kickoff_utc - timedelta(seconds=self.decision_lead_time_seconds)
        return prediction_time_utc < cutoff

    def get_cutoff_utc(self, scheduled_kickoff_utc: datetime) -> datetime:
        return scheduled_kickoff_utc - timedelta(seconds=self.decision_lead_time_seconds)


class PredictionDaemon:
    """Consumes authoritative fixtures and persists immutable prediction records."""

    def __init__(
        self,
        engine: DomainPredictionEngine | None = None,
        event_store: ImmutableEventStore | None = None,
        decision_lead_time_seconds: int = 3600,
        capture_mode: CaptureMode = CaptureMode.PRE_EPOCH_DRY_RUN,
        epoch_model_sha: str | None = None,
        config: AppConfig | None = None,
        activation_context: ProspectiveActivationContext | None = None,
    ):
        self.config = config or get_config()
        self.engine = engine or DomainPredictionEngine(config=self.config)
        self.event_store = event_store or ImmutableEventStore()
        self.capture_mode = capture_mode
        self.epoch_model_sha = epoch_model_sha

        # Invariant: Attempting to emit PROSPECTIVE evidence engages the hardened multi-condition guard
        if self.capture_mode == CaptureMode.PROSPECTIVE:
            ctx = activation_context or ProspectiveActivationContext(
                epoch_state="PRE_OBSERVATION_BLOCKED",
                epoch_model_sha=self.epoch_model_sha,
            )
            ProspectiveActivationGuard.verify(ctx)

        self.timing_contract = TimingContract(
            decision_lead_time_seconds=decision_lead_time_seconds,
            timing_contract_state="PRE_EPOCH_PROVISIONAL",
        )

    def predict_and_capture(
        self,
        fixture: FixtureStateProjection,
        fitted_models: dict[str, DixonColesModel],
        as_of_utc: datetime | None = None,
        training_data_identities: dict[str, str] | None = None,
        training_information_cutoffs_utc: dict[str, datetime | str] | None = None,
        model_fit_identities: dict[str, str] | None = None,
    ) -> ProspectiveEvent:
        """Score fixture and capture immutable prediction record in Stage 1 event store.

        Emits EventType.PREDICTION_CAPTURE_SUCCESS for valid forecasts and
        EventType.PREDICTION_CAPTURE_FAILURE for any rejection/ineligibility.
        """
        now_utc = as_of_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        # RESCHEDULED fixtures use latest authoritative schedule; SCHEDULED uses scheduled kickoff
        scheduled_kickoff = fixture.latest_scheduled_kickoff_utc
        if scheduled_kickoff.tzinfo is None:
            scheduled_kickoff = scheduled_kickoff.replace(tzinfo=timezone.utc)

        # 1. Fixture lifecycle gating (Requirement 4)
        lifecycle = getattr(fixture, "current_state", None) or getattr(fixture, "lifecycle_state", None)
        eligibility_state = "INELIGIBLE"
        failure_reason: str | None = None
        model_convergence_state = "UNKNOWN"

        if lifecycle == FixtureLifecycleState.POSTPONED:
            failure_reason = ModelFailureReason.LIFECYCLE_POSTPONED.value
            model_convergence_state = "NOT_EVALUATED"
        elif lifecycle == FixtureLifecycleState.STARTED:
            failure_reason = ModelFailureReason.LIFECYCLE_STARTED.value
            model_convergence_state = "NOT_EVALUATED"
        elif lifecycle in {
            FixtureLifecycleState.ABANDONED_PENDING_RULING,
            FixtureLifecycleState.COMPLETED,
            FixtureLifecycleState.OFFICIAL_RESULT_STANDS,
            FixtureLifecycleState.VOIDED,
        }:
            failure_reason = ModelFailureReason.LIFECYCLE_TERMINAL.value
            model_convergence_state = "NOT_EVALUATED"

        # 2. Timing contract check
        is_timing_valid = self.timing_contract.is_before_cutoff(now_utc, scheduled_kickoff)
        cutoff_utc = self.timing_contract.get_cutoff_utc(scheduled_kickoff)

        if failure_reason is None and not is_timing_valid:
            failure_reason = ModelFailureReason.POST_CUTOFF_ATTEMPT.value
            model_convergence_state = "NOT_EVALUATED"

        # 3. Model execution and training cutoff validation
        league = fixture.competition_id
        model = fitted_models.get(league) if failure_reason is None else None

        training_data_identity = (training_data_identities or {}).get(league)
        model_fit_identity = (model_fit_identities or {}).get(league)
        training_cutoff_val = (training_information_cutoffs_utc or {}).get(league)

        training_cutoff_str: str | None = None
        if training_cutoff_val is not None:
            if isinstance(training_cutoff_val, str):
                tc_dt = datetime.fromisoformat(training_cutoff_val.replace("Z", "+00:00"))
            else:
                tc_dt = training_cutoff_val
            if tc_dt.tzinfo is None:
                tc_dt = tc_dt.replace(tzinfo=timezone.utc)
            training_cutoff_str = tc_dt.isoformat()

            # Anti-look-ahead hard invariant: training cutoff must be strictly before prediction time
            if tc_dt >= now_utc:
                raise ValueError(
                    f"Anti-look-ahead invariant violated: training_information_cutoff_utc "
                    f"({tc_dt.isoformat()}) must be strictly before prediction_timestamp_utc "
                    f"({now_utc.isoformat()})."
                )

        p_home: float | None = None
        p_draw: float | None = None
        p_away: float | None = None
        diag_lambda: float | None = None
        diag_mu: float | None = None
        diag_rho: float | None = None

        if failure_reason is None:
            if model is None:
                failure_reason = ModelFailureReason.UNMODELED_LEAGUE.value
                model_convergence_state = "UNMODELED"
            elif not model.is_production_eligible:
                failure_reason = ModelFailureReason.MODEL_NON_CONVERGENCE.value
                model_convergence_state = "FAILED"
            else:
                model_convergence_state = "CONVERGED"
                try:
                    lam, mu, rho = model.predict(
                        fixture.home_team_id,
                        fixture.away_team_id,
                        allow_unseen=False,
                    )
                    probs = match_probabilities(lam, mu, rho)
                    p_home = float(probs["p_home"])
                    p_draw = float(probs["p_draw"])
                    p_away = float(probs["p_away"])
                    diag_lambda = float(lam)
                    diag_mu = float(mu)
                    diag_rho = float(rho)
                    eligibility_state = "ELIGIBLE"
                    failure_reason = None
                except UnknownTeamError:
                    failure_reason = ModelFailureReason.UNKNOWN_TEAM.value
                    eligibility_state = "INELIGIBLE"
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Prediction calculation failure for %s: %s", fixture.internal_fixture_id, exc)
                    failure_reason = ModelFailureReason.INSUFFICIENT_HISTORY.value
                    eligibility_state = "INELIGIBLE"

        # 4. Resolve model fit identity if model is eligible and fit identity not precomputed
        code_sha = get_code_identity()
        config_sha = get_semantic_config_identity()

        if eligibility_state == "ELIGIBLE" and model is not None and model_fit_identity is None:
            td_id = training_data_identity or "UNSPECIFIED_TRAINING_IDENTITY"
            model_fit_identity = compute_model_fit_identity(
                model=model,
                league=league,
                training_data_identity=td_id,
                semantic_config_identity=config_sha,
                code_identity=code_sha,
            )

        # 5. Distinct structural event types: PREDICTION_CAPTURE_SUCCESS vs PREDICTION_CAPTURE_FAILURE
        is_success = eligibility_state == "ELIGIBLE"
        event_type = EventType.PREDICTION_CAPTURE_SUCCESS if is_success else EventType.PREDICTION_CAPTURE_FAILURE
        capture_status = "PREDICTION_CAPTURE_SUCCESS" if is_success else "PREDICTION_CAPTURE_FAILURE"

        payload: dict[str, Any] = {
            "internal_fixture_id": fixture.internal_fixture_id,
            "competition_id": fixture.competition_id,
            "season_id": fixture.season_id,
            "home_team_id": fixture.home_team_id,
            "away_team_id": fixture.away_team_id,
            "capture_mode": self.capture_mode.value,
            "capture_status": capture_status,
            "prediction_timestamp_utc": now_utc.isoformat(),
            "scheduled_kickoff_utc": scheduled_kickoff.isoformat(),
            "actual_kickoff_utc": fixture.actual_kickoff_utc.isoformat() if fixture.actual_kickoff_utc else None,
            "actual_kickoff_status": fixture.actual_kickoff_status.value,
            "decision_lead_time_seconds": self.timing_contract.decision_lead_time_seconds,
            "cutoff_timestamp_utc": cutoff_utc.isoformat(),
            "timing_contract_state": self.timing_contract.timing_contract_state,
            "timing_contract_version": self.timing_contract.contract_version,
            "eligibility_state": eligibility_state,
            "failure_reason": failure_reason,
            "model_convergence_state": model_convergence_state,
            "training_data_identity": training_data_identity if is_success else None,
            "model_fit_identity": model_fit_identity if is_success else None,
            "training_information_cutoff_utc": training_cutoff_str,
            "forecast_evaluable": is_success,  # Invariant: failures are provably NOT evaluable
            "model_probabilities": {
                "p_home": p_home,
                "p_draw": p_draw,
                "p_away": p_away,
            } if is_success else None,
            "dixon_coles_diagnostics": {
                "lambda": diag_lambda,
                "mu": diag_mu,
                "rho": diag_rho,
            } if is_success else None,
            "prospective_eligible": False,  # Strict invariant for Stage 3
            "evidence_classification": "PRE_EPOCH_VALIDATION",
            "external_contract_identity": "ForecastCaptureContract::v1.0",
            "fixture_lifecycle_contract": "FixtureLifecycleContract::v1.0",
        }

        # 6. Idempotency key
        ikey = compute_idempotency_key(
            event_type=event_type,
            entity_id=fixture.internal_fixture_id,
            timestamp_str=scheduled_kickoff.isoformat(),
            qualifier=f"{self.capture_mode.value}:{capture_status}:{failure_reason or 'OK'}",
        )

        event = ProspectiveEvent(
            event_type=event_type,
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

    def get_prospective_denominator(self, epoch_id: str | None = None) -> int:
        """Prospective evaluation denominator guard.

        Strictly returns 0 in Stage 3: only formally active PROSPECTIVE events count.
        Capture failures are provably excluded.
        """
        events = self.event_store.list_prospective_events(epoch_id=epoch_id)
        evaluable = [
            e for e in events
            if e.event_type in {EventType.PREDICTION_CAPTURE_SUCCESS, EventType.PREDICTION_RECORD}
            and e.payload.get("forecast_evaluable", False) is True
            and e.payload.get("prospective_eligible", False) is True
            and e.test_flag == TestFlag.PROSPECTIVE
        ]
        return len(evaluable)
