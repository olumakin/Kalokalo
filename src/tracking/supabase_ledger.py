"""
Supabase-backed durable prediction ledger (PID Phase 0 "immediate
containment": an append-only remote audit trail, separate from the
local file ledger in src/tracking/ledger.py).

Writes here are *in addition to* the local ledger, not a replacement
for it — pages/3_Ledger.py's UI still reads the local file. This module
gives every prediction a second, durable home outside the container's
ephemeral disk, gated behind RLS policies that only allow INSERT/SELECT
with the anon (publishable) key — never UPDATE or DELETE, so a
prediction can't be silently altered after the fact. Settling a
fixture's actual result is a separate append-only insert into
`settlements`, not a mutation of the `predictions` row.

Schema + RLS policies to run once in the Supabase SQL editor: see
supabase/schema.sql at the repo root (this module has no DDL access
with only the anon/publishable key).

Deliberately UI-framework-agnostic (only `logging`, no `streamlit`),
matching every other src/ module — the caller (app.py) decides how to
surface failures, and this stays unit-testable without a Streamlit
runtime.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

MODEL_VERSION = "dixon-coles-v1"  # bump when the fitting method changes materially


def _resolve_credentials(url: str | None, key: str | None) -> tuple[str | None, str | None]:
    if url and key:
        return url, key
    url = url or os.environ.get("SUPABASE_URL")
    key = key or os.environ.get("SUPABASE_ANON_KEY")
    if url and key:
        return url, key
    try:
        import streamlit as st  # optional; only used as a last-resort credential source
        url = url or st.secrets.get("SUPABASE_URL")
        key = key or st.secrets.get("SUPABASE_ANON_KEY")
    except Exception:
        pass
    return url, key


def get_supabase_client(url: str | None = None, key: str | None = None):
    """Build a Supabase client from explicit args, then env vars, then
    Streamlit secrets. Returns None (logged warning) rather than raising
    if credentials are missing or the client can't be constructed —
    every write function below treats None as "ledger unavailable" and
    reports it back to the caller instead of crashing the pipeline."""
    url, key = _resolve_credentials(url, key)
    if not url or not key:
        logger.warning("Supabase credentials not configured; remote ledger writes will be skipped")
        return None

    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not construct Supabase client: %s", exc)
        return None


def prediction_row_from_pipeline(
    row: pd.Series, run_id: str, model, settings: dict, price_source: str,
) -> dict[str, Any]:
    """Map one src.pipeline.build_predictions() output row (plus the
    fitted model it came from) onto the Supabase `predictions` schema."""
    return {
        "run_id": run_id,
        "model_version": MODEL_VERSION,
        "fixture_id": row["match_id"],
        "match_date": pd.Timestamp(row["date"]).isoformat(),
        "league": row["league"],
        "season": None,
        "home_id": row["home_team"],
        "away_id": row["away_team"],
        "p_home": float(row["model_p_home"]),
        "p_draw": float(row["model_p_draw"]),
        "p_away": float(row["model_p_away"]),
        "lambda_val": float(row["xg_home"]),
        "mu_val": float(row["xg_away"]),
        "rho_val": float(model.rho_) if model is not None else None,
        "xi_val": float(settings["model"]["xi_decay"]) if settings else None,
        "converged": bool(model.converged_) if model is not None else None,
        "skip_reason": "fallback_to_independent_poisson" if (model is not None and model.fallback_used_) else None,
        "odds_entry": float(row["odds_draw"]),
        "price_source": price_source,
        "ev_entry": float(row["ev"]),
    }


def write_predictions(predictions: list[dict[str, Any]], client=None) -> int:
    """Upsert a batch of prediction rows, ignoring conflicts on the
    (fixture_id, model_version, run_id) unique constraint so a re-run
    against the same fixture card doesn't error or duplicate rows.

    Returns the number of rows that failed to write (0 on full success,
    len(predictions) if the client is unavailable or the call fails) —
    never raises, so a ledger outage never blocks showing predictions
    on screen.
    """
    if not predictions:
        return 0
    client = client or get_supabase_client()
    if client is None:
        return len(predictions)

    try:
        client.table("predictions").upsert(
            predictions, on_conflict="fixture_id,model_version,run_id", ignore_duplicates=True,
        ).execute()
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase predictions write failed for %d row(s): %s", len(predictions), exc)
        return len(predictions)


def write_settlement(
    fixture_id: str, odds_close: float | None, actual_result: str, client=None,
) -> bool:
    """Append one settlement row. Returns True on success, False (logged
    warning) on any failure — never raises."""
    client = client or get_supabase_client()
    if client is None:
        return False

    payload = {"fixture_id": fixture_id, "odds_close": odds_close, "actual_result": actual_result}
    try:
        client.table("settlements").insert(payload).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase settlement write failed for %s: %s", fixture_id, exc)
        return False
