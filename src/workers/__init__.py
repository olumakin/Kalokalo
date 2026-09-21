"""
Validation infrastructure workers (Stage 3).

Forecast and market benchmark daemons for pre-match validation evidence capture.
"""
from __future__ import annotations

from enum import Enum


class CaptureMode(str, Enum):
    TEST = "TEST"
    PRE_EPOCH_DRY_RUN = "PRE_EPOCH_DRY_RUN"
    PROSPECTIVE = "PROSPECTIVE"


class ProspectiveActivationBlockedError(RuntimeError):
    """Raised when attempting to activate PROSPECTIVE mode while pre-epoch gates are unratified or epoch SHA unassigned."""
    pass


from src.workers.activation_guard import (
    ProspectiveActivationContext,
    ProspectiveActivationGuard,
)
from src.workers.decision_daemon import (
    PAPER_DECISION_CONTRACT_VERSION,
    DecisionDaemon,
    DecisionFailureReason,
    compute_exposure_state_identity,
)

__all__ = [
    "CaptureMode",
    "ProspectiveActivationBlockedError",
    "ProspectiveActivationContext",
    "ProspectiveActivationGuard",
    "DecisionDaemon",
    "DecisionFailureReason",
    "PAPER_DECISION_CONTRACT_VERSION",
    "compute_exposure_state_identity",
]
