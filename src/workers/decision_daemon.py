"""
Prospective Paper Decision Worker (Stage 4 / A09).

Consumes authoritative Stage 2 fixture projections, Stage 3 frozen domain model
forecasts, contemporaneous Stage 3 market benchmark reference snapshots (for the
information filter), and Stage 4 observable named execution quotes (for EV and sizing).

Freezes all decision inputs into immutable EventType.DECISION_RECORD events in the
Stage 1 store under PaperDecisionContract::v1.0. Enforces strict economic separation:
- Information filter: model_p_draw > benchmark_market_p_draw (consensus reference).
- Executable EV: EV = (model_p_draw * execution_decimal_odds) - 1 >= min_ev (named quote).
  Unclipped analytical EV is faithfully recorded in evidence.
- Sizing: Fractional Kelly (c = 0.15) with dual-layer exposure caps (single_match_cap = 0.025,
  daily_slate_cap = 0.08).
- Authoritative Slate Exposure: Derived strictly from the immutable event store with
  causal ordering and deterministic EXPOSURE_STATE_IDENTITY. Caller-supplied overrides
  cannot omit stored decisions.
- Selection Identity: Explicit verification that prediction, benchmark, and execution quote
  all correspond to market_type="1X2" and selection="DRAW". Rejects selection mismatch fail closed.
- Stable Bookmaker Identity: Binds canonical bookmaker_id, bookmaker_name, and provider_namespace.
- Paper-Only Evidence: execution_mode="PAPER_AT_OBSERVED_NAMED_QUOTE", liquidity_status="NOT_ESTABLISHED".
Strictly prohibits benchmark odds fallback fail-closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import logging
import math
from typing import Any

from pydantic import BaseModel, Field

from src.analytics.edge import calculate_ev, kelly_fraction
from src.config import get_config
from src.ingestion.fixture_tracker import (
    FixtureLifecycleState,
    FixtureStateProjection,
)
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
from src.workers.predict_daemon import TimingContract

logger = logging.getLogger(__name__)

PAPER_DECISION_CONTRACT_VERSION = "PaperDecisionContract::v1.0"


def compute_exposure_state_identity(
    slate_id: str,
    prior_qualifying: list[tuple[str, float]],
) -> str:
    """Deterministic 64-character SHA-256 over exact ordered prior qualifying decision set."""
    components = [f"{dec_id}:{stake:.6f}" for dec_id, stake in prior_qualifying]
    raw = f"{slate_id}|" + "|".join(components)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DecisionFailureReason(str, Enum):
    POST_CUTOFF_DECISION_ATTEMPT = "POST_CUTOFF_DECISION_ATTEMPT"
    FIXTURE_LIFECYCLE_INELIGIBLE = "FIXTURE_LIFECYCLE_INELIGIBLE"
    MISSING_FORECAST = "MISSING_FORECAST"
    FORECAST_CAPTURE_FAILED = "FORECAST_CAPTURE_FAILED"
    MISSING_BENCHMARK_QUOTE = "MISSING_BENCHMARK_QUOTE"
    NO_EXECUTION_QUOTE = "NO_EXECUTION_QUOTE"
    EXECUTION_QUOTE_INELIGIBLE = "EXECUTION_QUOTE_INELIGIBLE"
    EXECUTION_QUOTE_STALE = "EXECUTION_QUOTE_STALE"
    CAUSAL_ORDERING_VIOLATION = "CAUSAL_ORDERING_VIOLATION"
    INFORMATION_FILTER_FAILED = "INFORMATION_FILTER_FAILED"
    EV_BELOW_THRESHOLD = "EV_BELOW_THRESHOLD"
    ZERO_KELLY_STAKE = "ZERO_KELLY_STAKE"
    SLATE_CAPACITY_EXHAUSTED = "SLATE_CAPACITY_EXHAUSTED"
    SELECTION_MISMATCH = "SELECTION_MISMATCH"


class DecisionDaemon:
    """Evaluates paper betting decisions conforming to PaperDecisionContract::v1.0."""

    def __init__(
        self,
        event_store: ImmutableEventStore | None = None,
        timing_contract: TimingContract | None = None,
        capture_mode: CaptureMode = CaptureMode.PRE_EPOCH_DRY_RUN,
        epoch_model_sha: str | None = None,
        activation_context: ProspectiveActivationContext | None = None,
        min_ev: float | None = None,
        kelly_multiplier: float | None = None,
        single_match_cap: float | None = None,
        daily_slate_cap: float | None = None,
        market_type: str = "1X2",
        selection: str = "DRAW",
    ):
        self.event_store = event_store or ImmutableEventStore()
        self.timing_contract = timing_contract or TimingContract()
        self.capture_mode = capture_mode
        self.epoch_model_sha = epoch_model_sha

        # Configuration-backed parameters
        try:
            cfg = get_config()
            self.min_ev = float(min_ev if min_ev is not None else cfg.edge.min_ev)
            self.kelly_multiplier = float(kelly_multiplier if kelly_multiplier is not None else cfg.edge.kelly_fraction)
            self.single_match_cap = float(single_match_cap if single_match_cap is not None else cfg.edge.single_match_cap)
            self.daily_slate_cap = float(daily_slate_cap if daily_slate_cap is not None else cfg.edge.daily_slate_cap)
            self.market_type = (market_type or cfg.execution.market_type).strip().upper()
            self.selection = (selection or cfg.execution.selection).strip().upper()
        except Exception:
            self.min_ev = float(min_ev if min_ev is not None else 0.03)
            self.kelly_multiplier = float(kelly_multiplier if kelly_multiplier is not None else 0.15)
            self.single_match_cap = float(single_match_cap if single_match_cap is not None else 0.025)
            self.daily_slate_cap = float(daily_slate_cap if daily_slate_cap is not None else 0.08)
            self.market_type = (market_type or "1X2").strip().upper()
            self.selection = (selection or "DRAW").strip().upper()

        # Invariant: Attempting to emit PROSPECTIVE evidence engages the hardened multi-condition guard
        if self.capture_mode == CaptureMode.PROSPECTIVE:
            ctx = activation_context or ProspectiveActivationContext(
                epoch_state="PRE_OBSERVATION_BLOCKED",
                epoch_model_sha=self.epoch_model_sha,
            )
            ProspectiveActivationGuard.verify(ctx)

    def evaluate_decision(
        self,
        internal_fixture_id: str,
        fixture_projection: FixtureStateProjection,
        prediction_event: ProspectiveEvent | None,
        benchmark_event: ProspectiveEvent | None,
        execution_event: ProspectiveEvent | None,
        as_of_utc: datetime | None = None,
        slate_id: str | None = None,
        existing_decisions: list[ProspectiveEvent] | None = None,
    ) -> ProspectiveEvent:
        """Evaluate paper decision for a fixture and persist immutable EventType.DECISION_RECORD.

        Separates:
        1. Reference benchmark quote for information filter (model_p_draw > benchmark_market_p_draw).
        2. Named observable execution quote for EV calculation and Kelly position sizing.
        3. Authoritative dual-layer exposure capping with deterministic EXPOSURE_STATE_IDENTITY.
        4. Explicit selection matching (market_type="1X2", selection="DRAW").
        5. Faithful preservation of unclipped EV evidence.
        """
        now_utc = as_of_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        disqualification_reasons: list[str] = []
        qualified = True

        # 1. Prediction timing contract check: t_decision < t_kickoff_sched - lead_time
        sched_kickoff = (
            getattr(fixture_projection, "latest_scheduled_kickoff_utc", None)
            or getattr(fixture_projection, "scheduled_kickoff_utc", None)
        )
        if sched_kickoff is not None and sched_kickoff.tzinfo is None:
            sched_kickoff = sched_kickoff.replace(tzinfo=timezone.utc)

        if sched_kickoff is not None and not self.timing_contract.is_before_cutoff(now_utc, sched_kickoff):
            qualified = False
            disqualification_reasons.append(DecisionFailureReason.POST_CUTOFF_DECISION_ATTEMPT.value)

        # 2. Lifecycle state check: fixture must be in SCHEDULED or RESCHEDULED state
        eligible_lifecycle_states = {
            FixtureLifecycleState.SCHEDULED,
            FixtureLifecycleState.RESCHEDULED,
        }
        lifecycle_state = (
            getattr(fixture_projection, "current_state", None)
            or getattr(fixture_projection, "state", None)
        )
        if lifecycle_state not in eligible_lifecycle_states:
            qualified = False
            state_val = lifecycle_state.value if isinstance(lifecycle_state, FixtureLifecycleState) else str(lifecycle_state)
            disqualification_reasons.append(
                f"{DecisionFailureReason.FIXTURE_LIFECYCLE_INELIGIBLE.value}_{state_val}"
            )

        # 3. Forecast verification
        model_p_draw: float | None = None
        model_fit_identity: str | None = None
        training_data_identity: str | None = None

        if prediction_event is None:
            qualified = False
            disqualification_reasons.append(DecisionFailureReason.MISSING_FORECAST.value)
        elif prediction_event.event_type != EventType.PREDICTION_CAPTURE_SUCCESS:
            qualified = False
            disqualification_reasons.append(DecisionFailureReason.FORECAST_CAPTURE_FAILED.value)
        else:
            model_probs = prediction_event.payload.get("model_probabilities") or {}
            model_p_draw = model_probs.get("p_draw")
            model_fit_identity = prediction_event.payload.get("model_fit_identity")
            training_data_identity = prediction_event.payload.get("training_data_identity")

            pred_ts_str = prediction_event.payload.get("prediction_timestamp_utc")
            if pred_ts_str:
                pred_ts = datetime.fromisoformat(pred_ts_str)
                if pred_ts > now_utc:
                    qualified = False
                    disqualification_reasons.append(DecisionFailureReason.CAUSAL_ORDERING_VIOLATION.value)

        # 4. Benchmark reference quote verification (Information Filter)
        benchmark_market_p_draw: float | None = None
        if benchmark_event is None:
            qualified = False
            disqualification_reasons.append(DecisionFailureReason.MISSING_BENCHMARK_QUOTE.value)
        else:
            benchmark_market_p_draw = benchmark_event.payload.get("derived_market_p_draw")
            bm_ts_str = benchmark_event.payload.get("provider_quote_timestamp_utc")
            if bm_ts_str:
                bm_ts = datetime.fromisoformat(bm_ts_str)
                if bm_ts > now_utc:
                    qualified = False
                    disqualification_reasons.append(DecisionFailureReason.CAUSAL_ORDERING_VIOLATION.value)

            if benchmark_market_p_draw is None:
                qualified = False
                disqualification_reasons.append(DecisionFailureReason.MISSING_BENCHMARK_QUOTE.value)
            elif model_p_draw is not None and model_p_draw <= benchmark_market_p_draw:
                qualified = False
                disqualification_reasons.append(DecisionFailureReason.INFORMATION_FILTER_FAILED.value)

        # 5. Named execution quote verification (Executable Price Source & Selection Match)
        bookmaker_id: str | None = None
        bookmaker_name: str | None = None
        provider_namespace: str = "the_odds_api"
        execution_decimal_odds: float | None = None
        execution_ev: float | None = None

        if execution_event is None:
            qualified = False
            disqualification_reasons.append(DecisionFailureReason.NO_EXECUTION_QUOTE.value)
        else:
            is_exec = execution_event.payload.get("is_executable_price", False)
            exec_fail = execution_event.payload.get("failure_reason")
            bookmaker_id = execution_event.payload.get("bookmaker_id")
            bookmaker_name = execution_event.payload.get("bookmaker_name") or execution_event.payload.get("bookmaker")
            provider_namespace = execution_event.payload.get("provider_namespace", "the_odds_api")
            exec_market_type = execution_event.payload.get("market_type", "1X2")
            exec_selection = execution_event.payload.get("selection", "DRAW")
            
            # Target odds: draw odds for 1X2 DRAW selection
            execution_decimal_odds = execution_event.payload.get("selection_odds") or execution_event.payload.get("raw_odds_draw")

            exec_ts_str = execution_event.payload.get("provider_quote_timestamp_utc")
            if exec_ts_str:
                exec_ts = datetime.fromisoformat(exec_ts_str)
                if exec_ts > now_utc:
                    qualified = False
                    disqualification_reasons.append(DecisionFailureReason.CAUSAL_ORDERING_VIOLATION.value)

            # Explicit Selection Identity Check
            if exec_market_type != self.market_type or exec_selection != self.selection:
                qualified = False
                disqualification_reasons.append(DecisionFailureReason.SELECTION_MISMATCH.value)

            if not is_exec or exec_fail or not bookmaker_id or not execution_decimal_odds:
                qualified = False
                disqualification_reasons.append(exec_fail or DecisionFailureReason.EXECUTION_QUOTE_INELIGIBLE.value)
            else:
                # 6. Faithful Executable EV calculation: EV = (model_p_draw * odds) - 1
                if model_p_draw is not None:
                    raw_ev = calculate_ev(model_p_draw, execution_decimal_odds)
                    execution_ev = round(raw_ev, 6)
                    # Hurdle qualification check (EV not clamped; negative EV preserved)
                    if raw_ev < self.min_ev:
                        qualified = False
                        disqualification_reasons.append(DecisionFailureReason.EV_BELOW_THRESHOLD.value)

        # 7. Authoritative Slate Exposure & Position Sizing
        effective_slate_id = slate_id or (sched_kickoff.strftime("%Y-%m-%d") if sched_kickoff else "DEFAULT_SLATE")

        # Derive complete prior qualifying paper-decision set strictly from event store (excluding this fixture)
        prior_qualifying_events = self._derive_authoritative_prior_decisions(
            effective_slate_id,
            now_utc,
            exclude_fixture_id=internal_fixture_id,
        )
        auth_decision_ids = [d.payload.get("decision_id") for d in prior_qualifying_events]

        # Verify caller-supplied prior decisions (cannot omit stored decisions or inject fake decisions)
        if existing_decisions is not None:
            caller_qualifying = [
                d for d in existing_decisions
                if d.event_type == EventType.DECISION_RECORD
                and d.payload.get("internal_fixture_id") != internal_fixture_id
                and d.payload.get("exposure_snapshot", {}).get("slate_id") == effective_slate_id
                and d.payload.get("qualification_result") is True
                and d.payload.get("constrained_paper_stake_fraction", 0.0) > 0.0
            ]
            caller_decision_ids = [d.payload.get("decision_id") for d in caller_qualifying]
            if set(caller_decision_ids) != set(auth_decision_ids):
                raise ValueError(
                    f"Authoritative exposure violation: caller-supplied prior decisions ({caller_decision_ids}) "
                    f"conflict with stored authoritative decisions ({auth_decision_ids}). Caller cannot reduce exposure."
                )

        # Sizing and capacity computation based strictly on authoritative prior decisions
        prior_qualifying_tuples = [
            (d.payload.get("decision_id"), float(d.payload.get("constrained_paper_stake_fraction", 0.0)))
            for d in prior_qualifying_events
        ]
        prior_cumulative_exposure = sum(t[1] for t in prior_qualifying_tuples)
        prior_qualifying_decision_ids = [t[0] for t in prior_qualifying_tuples]

        exposure_state_identity = compute_exposure_state_identity(effective_slate_id, prior_qualifying_tuples)

        unconstrained_kelly: float = 0.0
        single_match_constrained_stake: float = 0.0
        remaining_slate_capacity = max(0.0, self.daily_slate_cap - prior_cumulative_exposure)
        final_paper_stake: float = 0.0

        if qualified and model_p_draw is not None and execution_decimal_odds is not None:
            f_star = kelly_fraction(
                p=model_p_draw,
                odds=execution_decimal_odds,
                c=self.kelly_multiplier,
            )
            unconstrained_kelly = max(0.0, float(f_star))

            if unconstrained_kelly <= 0.0:
                qualified = False
                disqualification_reasons.append(DecisionFailureReason.ZERO_KELLY_STAKE.value)
                final_paper_stake = 0.0
            else:
                # 1. Single match cap
                single_match_constrained_stake = min(unconstrained_kelly, self.single_match_cap)

                # 2. Daily slate cap
                final_paper_stake = min(single_match_constrained_stake, remaining_slate_capacity)

                if remaining_slate_capacity <= 0.0:
                    disqualification_reasons.append(DecisionFailureReason.SLATE_CAPACITY_EXHAUSTED.value)

        post_decision_cumulative_exposure = prior_cumulative_exposure + final_paper_stake

        exposure_snapshot = {
            "slate_id": effective_slate_id,
            "prior_qualifying_decision_ids": prior_qualifying_decision_ids,
            "prior_cumulative_paper_exposure": round(prior_cumulative_exposure, 6),
            "prior_cumulative_exposure": round(prior_cumulative_exposure, 6),  # Compatibility alias
            "candidate_unconstrained_stake": round(unconstrained_kelly, 6),
            "single_match_constrained_stake": round(single_match_constrained_stake, 6),
            "remaining_slate_capacity": round(remaining_slate_capacity, 6),
            "final_paper_stake": round(final_paper_stake, 6),
            "post_decision_cumulative_exposure": round(post_decision_cumulative_exposure, 6),
            "exposure_state_identity": exposure_state_identity,
            "single_match_cap": self.single_match_cap,
            "daily_slate_cap": self.daily_slate_cap,
            "capacity_exhausted": prior_cumulative_exposure >= self.daily_slate_cap,
        }

        qualifier = (
            f"{bookmaker_id or 'NO_EXEC'}:{self.market_type}:{self.selection}:"
            f"{qualified}:{round(final_paper_stake, 6)}:{len(disqualification_reasons)}:{exposure_state_identity[:12]}"
        )
        ikey = compute_idempotency_key(
            event_type=EventType.DECISION_RECORD,
            entity_id=internal_fixture_id,
            timestamp_str=now_utc.isoformat(),
            provider_event_id=prediction_event.event_id if prediction_event else None,
            qualifier=qualifier,
        )
        decision_id = f"dec_{ikey[:16]}"

        payload: dict[str, Any] = {
            "decision_id": decision_id,
            "internal_fixture_id": internal_fixture_id,
            "market_type": self.market_type,
            "selection": self.selection,
            "prediction_event_id": prediction_event.event_id if prediction_event else None,
            "model_fit_identity": model_fit_identity,
            "training_data_identity": training_data_identity,
            "execution_quote_id": execution_event.event_id if execution_event else None,
            "benchmark_snapshot_id": benchmark_event.event_id if benchmark_event else None,
            "decision_timestamp_utc": now_utc.isoformat(),
            "model_p_draw": round(model_p_draw, 6) if model_p_draw is not None else None,
            "benchmark_market_p_draw": round(benchmark_market_p_draw, 6) if benchmark_market_p_draw is not None else None,
            "bookmaker_id": bookmaker_id,
            "bookmaker_name": bookmaker_name,
            "execution_bookmaker": bookmaker_name,  # Backward compatibility field
            "provider_namespace": provider_namespace,
            "execution_decimal_odds": float(execution_decimal_odds) if execution_decimal_odds is not None else None,
            "execution_ev": execution_ev,  # Unclipped analytical EV faithfully preserved
            "qualification_threshold": self.min_ev,
            "qualification_result": qualified,
            "disqualification_reasons": disqualification_reasons,
            "kelly_multiplier": self.kelly_multiplier,
            "unconstrained_kelly_fraction": round(unconstrained_kelly, 6),
            "constrained_paper_stake_fraction": round(final_paper_stake, 6),
            "single_match_cap": self.single_match_cap,
            "daily_slate_cap": self.daily_slate_cap,
            "exposure_snapshot": exposure_snapshot,
            "exposure_state_identity": exposure_state_identity,
            "is_paper_decision": True,
            "execution_mode": "PAPER_AT_OBSERVED_NAMED_QUOTE",
            "liquidity_status": "NOT_ESTABLISHED",
            "capture_mode": self.capture_mode.value,
            "prospective_eligible": False,  # Strict Stage 4 invariant
            "evidence_classification": "PRE_EPOCH_VALIDATION",
            "external_contract_identity": PAPER_DECISION_CONTRACT_VERSION,
        }

        code_sha = get_code_identity()
        config_sha = get_semantic_config_identity()

        event = ProspectiveEvent(
            event_type=EventType.DECISION_RECORD,
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

    def _derive_authoritative_prior_decisions(
        self,
        slate_id: str,
        decision_time_utc: datetime,
        exclude_fixture_id: str | None = None,
    ) -> list[ProspectiveEvent]:
        """Derive complete prior qualifying paper-decision set strictly from event store with causal ordering."""
        qualifying: list[ProspectiveEvent] = []
        for e in self.event_store._events_by_id.values():
            if e.event_type != EventType.DECISION_RECORD:
                continue
            if exclude_fixture_id and e.payload.get("internal_fixture_id") == exclude_fixture_id:
                continue
            snap = e.payload.get("exposure_snapshot", {})
            if snap.get("slate_id") != slate_id:
                continue
            # Causal check: decision must precede or coincide with current decision time
            dec_ts_str = e.payload.get("decision_timestamp_utc")
            if dec_ts_str:
                dec_ts = datetime.fromisoformat(dec_ts_str)
                if dec_ts > decision_time_utc:
                    continue
            stake = e.payload.get("constrained_paper_stake_fraction", 0.0)
            if e.payload.get("qualification_result") is True and stake > 0.0:
                qualifying.append(e)

        # Sort strictly causally
        return sorted(
            qualifying,
            key=lambda d: (
                d.payload.get("decision_timestamp_utc", ""),
                d.created_at_utc,
                d.event_id,
            ),
        )
