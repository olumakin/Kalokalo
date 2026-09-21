"""
Deterministic training data and model fit provenance engine (Stage 3).

Binds predictive forecasts to exact historical training observations and
full fitted model parameter state for forensic reproducibility.
Enforces strict information-availability semantics:
result_available_at_utc < training_information_cutoff_utc < prediction_timestamp_utc.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from src.models.dixon_coles import DixonColesModel


class ResultAvailabilityProvenance(str, Enum):
    """Authoritative provenance categories for final match result availability."""

    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    OFFICIALLY_VERIFIED = "OFFICIALLY_VERIFIED"
    FIRST_OBSERVED_FINAL_RESULT = "FIRST_OBSERVED_FINAL_RESULT"
    CONSERVATIVE_DERIVED = "CONSERVATIVE_DERIVED"
    UNKNOWN = "UNKNOWN"


def _to_utc_iso(dt_val: Any) -> str:
    """Normalize datetime or timestamp representation to canonical ISO 8601 UTC string."""
    if isinstance(dt_val, str):
        parsed = datetime.fromisoformat(dt_val.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    if isinstance(dt_val, pd.Timestamp):
        if dt_val.tzinfo is None:
            dt_val = dt_val.tz_localize("UTC")
        else:
            dt_val = dt_val.tz_convert("UTC")
        return dt_val.isoformat()
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            dt_val = dt_val.replace(tzinfo=timezone.utc)
        return dt_val.astimezone(timezone.utc).isoformat()
    parsed = pd.to_datetime(dt_val, utc=True)
    return parsed.isoformat()


def compute_training_data_identity(
    matches: pd.DataFrame,
    league: str,
    training_information_cutoff_utc: datetime | str,
    training_window_start_utc: datetime | str | None = None,
    xi: float | None = None,
    goal_columns: tuple[str, str] = ("home_goals", "away_goals"),
) -> str:
    """Compute deterministic SHA-256 fingerprint of the historical training dataset.

    Enforces strict information-availability semantics:
    Every match in the training set must have:
    result_available_at_utc < training_information_cutoff_utc

    Binds:
    - league partition
    - canonical training-window start
    - canonical training-information cutoff
    - match count
    - canonical match identifiers and outcomes
    - exact result availability timestamp and provenance status
    - weight-relevant timestamp
    - fitting inputs (xi decay, goal columns)

    Row reordering of the input DataFrame is mathematically irrelevant and does
    not change the resulting hash. Any material observation or provenance change does.
    """
    cutoff_iso = _to_utc_iso(training_information_cutoff_utc)
    cutoff_dt = datetime.fromisoformat(cutoff_iso)

    # Filter for league if league column is present
    df = matches
    if "league" in df.columns:
        df = df[df["league"] == league]

    home_col, away_col = goal_columns
    canonical_matches: list[dict[str, Any]] = []

    for idx, row in df.iterrows():
        raw_date = row.get("date") or row.get("match_date") or row.get("kickoff_utc")
        home_team = str(row.get("home_team", ""))
        away_team = str(row.get("away_team", ""))
        season = str(row.get("season", ""))
        match_id = str(row.get("match_id", f"{str(raw_date)[:10]}_{home_team}_{away_team}"))

        # 1. Unresolved or non-final match state check (Requirement 6)
        match_status = (
            row.get("status")
            or row.get("match_status")
            or row.get("reported_state")
            or row.get("lifecycle_state")
        )
        if match_status is not None:
            status_str = str(match_status).strip().upper()
            if status_str in {
                "ABANDONED_PENDING_RULING",
                "ABANDONED",
                "POSTPONED",
                "STARTED",
                "SCHEDULED",
                "RESCHEDULED",
                "VOIDED",
            }:
                raise ValueError(
                    f"Unresolved or non-final match state '{status_str}' cannot enter training "
                    f"observations for match {match_id}. Only completed, official results are eligible."
                )

        # 2. Result availability timestamp & provenance check (Requirements 1 & 2)
        raw_avail = row.get("result_available_at_utc") or row.get("result_available_at")
        raw_prov = row.get("result_availability_provenance") or row.get("result_availability_status")

        if raw_avail is None or (isinstance(raw_avail, float) and np.isnan(raw_avail)) or str(raw_avail).strip() == "":
            raise ValueError(
                f"Unknown result availability fails closed: match {match_id} lacks result_available_at_utc. "
                "Date-only inference is strictly prohibited."
            )

        prov_str = str(raw_prov).strip().upper() if raw_prov is not None else ResultAvailabilityProvenance.UNKNOWN.value
        if prov_str == ResultAvailabilityProvenance.UNKNOWN.value:
            raise ValueError(
                f"Unknown result availability fails closed: match {match_id} has UNKNOWN provenance. "
                "Prospective fitting requires verified result availability provenance."
            )

        valid_provenances = {
            ResultAvailabilityProvenance.PROVIDER_REPORTED.value,
            ResultAvailabilityProvenance.OFFICIALLY_VERIFIED.value,
            ResultAvailabilityProvenance.FIRST_OBSERVED_FINAL_RESULT.value,
            ResultAvailabilityProvenance.CONSERVATIVE_DERIVED.value,
        }
        if prov_str not in valid_provenances:
            raise ValueError(
                f"Invalid result availability provenance '{prov_str}' for match {match_id}. "
                f"Must be one of {sorted(valid_provenances)}"
            )

        avail_iso = _to_utc_iso(raw_avail)
        avail_dt = datetime.fromisoformat(avail_iso)

        # 3. Anti-look-ahead information boundary check (Requirement 1 & 3)
        # Invariant: result_available_at_utc < training_information_cutoff_utc
        if avail_dt >= cutoff_dt:
            raise ValueError(
                f"Temporal look-ahead violation: match {match_id} result available at {avail_iso} "
                f"is on or after training information cutoff {cutoff_iso}. "
                "Information-time boundary strictly excludes future results."
            )

        weight_date_iso = _to_utc_iso(raw_date) if raw_date is not None else avail_iso
        hg = row.get(home_col)
        ag = row.get(away_col)
        gh = int(hg) if pd.notnull(hg) else None
        ga = int(ag) if pd.notnull(ag) else None

        canonical_matches.append({
            "id": match_id,
            "home": home_team,
            "away": away_team,
            "gh": gh,
            "ga": ga,
            "season": season,
            "weight_timestamp_utc": weight_date_iso,
            "result_available_at_utc": avail_iso,
            "result_availability_provenance": prov_str,
        })

    # Canonical sorting: ordered by result availability time, weight timestamp, home, away, id
    canonical_matches.sort(
        key=lambda m: (
            m["result_available_at_utc"],
            m["weight_timestamp_utc"],
            m["home"],
            m["away"],
            m["id"],
        )
    )

    if training_window_start_utc is not None:
        window_start_iso = _to_utc_iso(training_window_start_utc)
    elif canonical_matches:
        window_start_iso = canonical_matches[0]["weight_timestamp_utc"]
    else:
        window_start_iso = cutoff_iso

    canonical_payload: dict[str, Any] = {
        "league": str(league),
        "training_window_start_utc": window_start_iso,
        "training_information_cutoff_utc": cutoff_iso,
        "match_count": len(canonical_matches),
        "matches": canonical_matches,
        "fitting_inputs": {
            "xi_decay": float(xi) if xi is not None else None,
            "goal_columns": list(goal_columns),
        },
    }

    canonical_bytes = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    return hashlib.sha256(canonical_bytes).hexdigest()


def compute_model_fit_identity(
    model: DixonColesModel,
    league: str,
    training_data_identity: str,
    semantic_config_identity: str,
    code_identity: str,
    fitting_settings: dict[str, Any] | None = None,
) -> str:
    """Compute deterministic SHA-256 fingerprint identifying exact fitted league model state.

    Binds:
    - TRAINING_DATA_IDENTITY (which incorporates result-availability timestamps and provenance)
    - semantic configuration identity
    - code identity
    - league partition
    - model fitting settings
    - full fitted parameter state (mu0, gamma, gamma_by_season, rho, alpha, beta, reference team)
    - convergence status
    """
    param_fingerprint: dict[str, Any] = {
        "mu0": f"{model.mu0_:.8f}" if getattr(model, "mu0_", None) is not None else None,
        "gamma": f"{model.gamma_:.8f}" if getattr(model, "gamma_", None) is not None else None,
        "rho": f"{model.rho_:.8f}" if getattr(model, "rho_", None) is not None else None,
        "reference_team": getattr(model, "reference_team_", None),
        "converged": bool(getattr(model, "converged_", False)),
        "fallback_used": bool(getattr(model, "fallback_used_", False)),
        "is_production_eligible": bool(getattr(model, "is_production_eligible", False)),
    }

    if hasattr(model, "gamma_by_season_") and model.gamma_by_season_:
        param_fingerprint["gamma_by_season"] = {
            k: f"{v:.8f}" for k, v in sorted(model.gamma_by_season_.items())
        }
    else:
        param_fingerprint["gamma_by_season"] = {}

    if hasattr(model, "alpha_") and model.alpha_:
        param_fingerprint["alpha"] = {
            k: f"{v:.8f}" for k, v in sorted(model.alpha_.items())
        }
    else:
        param_fingerprint["alpha"] = {}

    if hasattr(model, "beta_") and model.beta_:
        param_fingerprint["beta"] = {
            k: f"{v:.8f}" for k, v in sorted(model.beta_.items())
        }
    else:
        param_fingerprint["beta"] = {}

    payload: dict[str, Any] = {
        "training_data_identity": training_data_identity,
        "semantic_config_identity": semantic_config_identity,
        "code_identity": code_identity,
        "league": str(league),
        "fitting_settings": fitting_settings or {},
        "parameter_fingerprint": param_fingerprint,
        "convergence_status": "CONVERGED" if getattr(model, "converged_", False) else "FAILED",
    }

    canonical_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    return hashlib.sha256(canonical_bytes).hexdigest()
