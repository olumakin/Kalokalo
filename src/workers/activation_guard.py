"""
Prospective Activation Guard (Stage 3).

Enforces fail-closed multi-condition validation before prospective observation mode
can be activated. Rejects any attempt to emit prospective evidence without
formal epoch freeze, clean working tree, verified identities, and complete provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.validation.manifest import (
    EXTERNAL_CONTRACT_IDENTITIES,
    HEX_40_REGEX,
    EpochManifest,
    get_code_identity,
    get_semantic_config_identity,
    is_working_tree_dirty,
)
from src.workers import ProspectiveActivationBlockedError


@dataclass(frozen=True)
class ProspectiveActivationContext:
    """Context holding manifest conditions for prospective activation."""

    epoch_state: str = "PRE_OBSERVATION_BLOCKED"
    epoch_model_sha: str | None = None
    expected_head_sha: str | None = None
    expected_semantic_config_identity: str | None = None
    expected_contracts: dict[str, str] | None = None
    training_data_provenance: dict[str, str] | None = None
    activation_manifest: EpochManifest | None = None
    allow_dirty: bool = False
    repo_dir: Path | str | None = None


class ProspectiveActivationGuard:
    """Multi-condition guard enforcing fail-closed prospective observation gates."""

    @staticmethod
    def verify(context: ProspectiveActivationContext) -> None:
        """Verify all mandatory conditions for prospective activation.

        Raises ProspectiveActivationBlockedError on any violation.
        """
        # 1. EPOCH_STATE must be ACTIVE
        if context.epoch_state != "ACTIVE":
            raise ProspectiveActivationBlockedError(
                f"Prospective activation blocked: EPOCH_STATE must be 'ACTIVE', "
                f"got '{context.epoch_state}'."
            )

        # 2. EPOCH_MODEL_SHA must be assigned valid 40-character hex Git SHA
        if (
            not context.epoch_model_sha
            or context.epoch_model_sha == "UNASSIGNED"
            or not HEX_40_REGEX.match(context.epoch_model_sha)
        ):
            raise ProspectiveActivationBlockedError(
                f"Prospective activation blocked: EPOCH_MODEL_SHA must be an assigned "
                f"40-character hex Git SHA, got '{context.epoch_model_sha}'."
            )

        # 3. RUNTIME_HEAD_SHA must match EPOCH_MODEL_SHA
        runtime_head = get_code_identity(repo_dir=context.repo_dir)
        if runtime_head != context.epoch_model_sha:
            raise ProspectiveActivationBlockedError(
                f"Prospective activation blocked: RUNTIME_HEAD_SHA ({runtime_head}) "
                f"does not match EPOCH_MODEL_SHA ({context.epoch_model_sha})."
            )

        # 4. WORKING_TREE must be clean
        if not context.allow_dirty and is_working_tree_dirty(repo_dir=context.repo_dir):
            raise ProspectiveActivationBlockedError(
                "Prospective activation blocked: WORKING_TREE is dirty. "
                "Clean working tree required for prospective activation."
            )

        # 5. SEMANTIC_CONFIG_IDENTITY must match expected
        current_config_identity = get_semantic_config_identity()
        if (
            context.expected_semantic_config_identity
            and current_config_identity != context.expected_semantic_config_identity
        ):
            raise ProspectiveActivationBlockedError(
                f"Prospective activation blocked: SEMANTIC_CONFIG_IDENTITY drift: "
                f"runtime={current_config_identity}, expected={context.expected_semantic_config_identity}."
            )

        # 6. EXTERNAL_CONTRACT_IDENTITIES must match expected
        if context.expected_contracts:
            for contract_name, expected_version in context.expected_contracts.items():
                actual_version = EXTERNAL_CONTRACT_IDENTITIES.get(contract_name)
                if actual_version != expected_version:
                    raise ProspectiveActivationBlockedError(
                        f"Prospective activation blocked: Contract drift on '{contract_name}': "
                        f"runtime='{actual_version}', expected='{expected_version}'."
                    )

        # 7. TRAINING_DATA_PROVENANCE must be available and non-empty
        if not context.training_data_provenance:
            raise ProspectiveActivationBlockedError(
                "Prospective activation blocked: TRAINING_DATA_PROVENANCE is missing or empty. "
                "Fitted models must be bound to verifiable training data identities."
            )

        # 8. ACTIVATION_MANIFEST must be valid and non-test
        if context.activation_manifest is not None and context.activation_manifest.is_test:
            raise ProspectiveActivationBlockedError(
                "Prospective activation blocked: Activation manifest is marked as test."
            )
