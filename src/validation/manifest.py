"""
Deterministic provenance and epoch identity engine for prospective validation (Stage 1).

Binds observation events to CODE_IDENTITY (Git commit), SEMANTIC_CONFIG_IDENTITY (canonical
SHA-256 of runtime parameters affecting inference/decisions), and EXTERNAL_CONTRACT_IDENTITY.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from src.config import AppConfig, get_config


EXTERNAL_CONTRACT_IDENTITIES: dict[str, str] = {
    "price_source_contract": "PriceSourceContract::v1.0",
    "settlement_contract": "SettlementContract::v1.0",
    "closing_line_contract": "ClosingLineContract::v1.0",
    "fixture_lifecycle_contract": "FixtureLifecycleContract::v1.0",
    "forecast_capture_contract": "ForecastCaptureContract::v1.0",
    "market_benchmark_contract": "MarketBenchmarkContract::v1.0",
    "forecast_timing_contract": "ForecastTimingContract::v1.0",
    "execution_quote_contract": "ExecutionQuoteContract::v1.0",
    "paper_decision_contract": "PaperDecisionContract::v1.0",
}

HEX_40_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")
HEX_64_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")


def get_code_identity(repo_dir: Path | str | None = None) -> str:
    """Return the 40-character commit hash of HEAD from git."""
    cmd = ["git", "rev-parse", "HEAD"]
    cwd = str(repo_dir) if repo_dir else str(Path(__file__).resolve().parents[2])
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
        sha = res.stdout.strip()
        if not HEX_40_REGEX.match(sha):
            raise ValueError(f"Invalid Git SHA returned: {sha}")
        return sha
    except Exception as exc:
        raise RuntimeError(f"Failed to determine CODE_IDENTITY from repository at {cwd}: {exc}") from exc


def is_working_tree_dirty(repo_dir: Path | str | None = None) -> bool:
    """Return True if git working tree has uncommitted tracked or untracked changes."""
    cmd = ["git", "status", "--porcelain"]
    cwd = str(repo_dir) if repo_dir else str(Path(__file__).resolve().parents[2])
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
        return bool(res.stdout.strip())
    except Exception as exc:
        raise RuntimeError(f"Failed to inspect working tree status at {cwd}: {exc}") from exc


def extract_semantic_state(config: AppConfig | dict | None = None, team_mappings: dict | None = None) -> dict[str, Any]:
    """Extract runtime state fields that strictly affect predictive or decision semantics."""
    if config is None:
        cfg = get_config()
        cfg_dict = cfg.model_dump()
    elif isinstance(config, AppConfig):
        cfg_dict = config.model_dump()
    elif isinstance(config, dict):
        cfg_dict = config
    else:
        raise TypeError(f"Unsupported config type: {type(config)}")

    # Extract strictly semantic parameters:
    # Model: rolling window, team rating cutoff, decay rate, optimizer settings, grid limits
    m = cfg_dict.get("model", {})
    model_semantic = {
        "rolling_window_days": m.get("rolling_window_days"),
        "min_matches_for_team_rating": m.get("min_matches_for_team_rating"),
        "xi_decay": m.get("xi_decay"),
        "goal_grid_size": m.get("goal_grid_size"),
        "max_optimizer_iterations": m.get("max_optimizer_iterations"),
        "optimizer_method": m.get("optimizer_method"),
        "rho_init": m.get("rho_init"),
    }

    # Devig method
    d = cfg_dict.get("devig", {})
    devig_semantic = {
        "method": d.get("method"),
    }

    # Edge and Risk policy: hurdles, sizing, exposure caps
    e = cfg_dict.get("edge", {})
    kelly_val = e.get("kelly_fraction") if e.get("kelly_fraction") is not None else e.get("kelly_multiplier")
    if kelly_val is None and "kelly_multiplier" in cfg_dict:
        kelly_val = cfg_dict["kelly_multiplier"]

    slate_cap_val = e.get("daily_slate_cap") if e.get("daily_slate_cap") is not None else e.get("slate_cap")
    if slate_cap_val is None and "slate_cap" in cfg_dict:
        slate_cap_val = cfg_dict["slate_cap"]

    edge_semantic = {
        "min_ev": e.get("min_ev") if e.get("min_ev") is not None else cfg_dict.get("min_ev"),
        "kelly_fraction": kelly_val,
        "single_match_cap": e.get("single_match_cap") if e.get("single_match_cap") is not None else cfg_dict.get("single_match_cap"),
        "daily_slate_cap": slate_cap_val,
    }

    # Execution and Decision policy (Stage 4 / A09)
    x = cfg_dict.get("execution", {})
    exec_source = x.get("execution_price_source") or cfg_dict.get("execution_price_source") or "NAMED_OBSERVABLE_BOOKMAKER"
    bench_role = x.get("benchmark_price_role") or cfg_dict.get("benchmark_price_role") or "REFERENCE_INFORMATION_FILTER_ONLY"
    allow_fallback = x.get("allow_benchmark_fallback") if x.get("allow_benchmark_fallback") is not None else cfg_dict.get("allow_benchmark_fallback", False)

    max_age = x.get("max_execution_quote_age_seconds")
    if max_age is None:
        max_age = x.get("max_execution_quote_age")
    if max_age is None:
        max_age = cfg_dict.get("max_execution_quote_age_seconds")
    if max_age is None:
        max_age = cfg_dict.get("max_execution_quote_age", 900)

    lead_time = x.get("decision_lead_time_seconds") or cfg_dict.get("decision_lead_time_seconds", 3600)
    mkt_type = x.get("market_type") or cfg_dict.get("market_type", "1X2")
    sel = x.get("selection") or cfg_dict.get("selection", "DRAW")
    exec_mode = x.get("execution_mode") or cfg_dict.get("execution_mode", "PAPER_AT_OBSERVED_NAMED_QUOTE")
    mkt_filter_policy = x.get("market_relative_filter_policy") or cfg_dict.get("market_relative_filter_policy", "MODEL_DRAW_PROB_EXCEEDS_BENCHMARK_DEVIGGED")

    execution_semantic = {
        "execution_price_source": exec_source,
        "benchmark_price_role": bench_role,
        "allow_benchmark_fallback": allow_fallback,
        "max_execution_quote_age_seconds": max_age,
        "decision_lead_time_seconds": lead_time,
        "market_type": mkt_type,
        "selection": sel,
        "execution_mode": exec_mode,
        "market_relative_filter_policy": mkt_filter_policy,
    }

    # Leagues mapping
    leagues_semantic = cfg_dict.get("leagues", {})

    state: dict[str, Any] = {
        "leagues": leagues_semantic,
        "model": model_semantic,
        "devig": devig_semantic,
        "edge": edge_semantic,
        "execution": execution_semantic,
    }

    if team_mappings is not None:
        state["team_mappings"] = team_mappings

    return state


def get_semantic_config_identity(
    config: AppConfig | dict | None = None,
    team_mappings: dict | None = None,
) -> str:
    """Generate deterministic SHA-256 digest of semantic configuration state using canonical JSON."""
    state = extract_semantic_state(config=config, team_mappings=team_mappings)
    # Canonical JSON: keys sorted recursively, compact separators, UTF-8 encoded
    canonical_bytes = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


class EpochManifest(BaseModel):
    """Immutable identity manifest binding code, semantic config, and external contracts."""

    code_identity: str
    working_tree_dirty: bool
    semantic_config_identity: str
    external_contracts: dict[str, str] = Field(default_factory=lambda: dict(EXTERNAL_CONTRACT_IDENTITIES))
    epoch_id: str | None = None
    is_test: bool = False

    @field_validator("code_identity")
    @classmethod
    def validate_code_identity(cls, v: str) -> str:
        if "placeholder" in v.lower():
            raise ValueError("Placeholder hashes are strictly prohibited in operational manifests")
        if not HEX_40_REGEX.match(v):
            raise ValueError(f"code_identity must be 40-character hex Git SHA, got '{v}'")
        return v

    @field_validator("semantic_config_identity")
    @classmethod
    def validate_semantic_config(cls, v: str) -> str:
        if "placeholder" in v.lower():
            raise ValueError("Placeholder hashes are strictly prohibited in operational manifests")
        if not HEX_64_REGEX.match(v):
            raise ValueError(f"semantic_config_identity must be 64-character SHA-256 hex string, got '{v}'")
        return v

    @model_validator(mode="after")
    def validate_epoch_id_presence(self) -> EpochManifest:
        if not self.is_test and not self.epoch_id:
            raise ValueError("epoch_id is mandatory for prospective operational manifests (is_test=False)")
        return self



def create_epoch_manifest(
    epoch_id: str | None = None,
    repo_dir: Path | str | None = None,
    config: AppConfig | dict | None = None,
    team_mappings: dict | None = None,
    contracts: dict[str, str] | None = None,
    is_test: bool = False,
) -> EpochManifest:
    """Construct an EpochManifest verifying Git identity and canonical config hash."""
    code_sha = get_code_identity(repo_dir=repo_dir)
    dirty = is_working_tree_dirty(repo_dir=repo_dir)
    config_hash = get_semantic_config_identity(config=config, team_mappings=team_mappings)
    ext_contracts = contracts or dict(EXTERNAL_CONTRACT_IDENTITIES)

    return EpochManifest(
        code_identity=code_sha,
        working_tree_dirty=dirty,
        semantic_config_identity=config_hash,
        external_contracts=ext_contracts,
        epoch_id=epoch_id,
        is_test=is_test,
    )


from src.validation.provenance import (
    ResultAvailabilityProvenance,
    compute_model_fit_identity,
    compute_training_data_identity,
)

__all__ = [
    "EXTERNAL_CONTRACT_IDENTITIES",
    "EpochManifest",
    "create_epoch_manifest",
    "get_code_identity",
    "get_semantic_config_identity",
    "is_working_tree_dirty",
    "compute_training_data_identity",
    "compute_model_fit_identity",
    "ResultAvailabilityProvenance",
]
