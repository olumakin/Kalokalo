"""
Supabase-backed durable prediction ledger.

Supabase is the only ledger (Phase 0 revised) — the local file ledger
(src/tracking/ledger.py) has been retired; see
scripts/migrate_local_ledger_to_supabase.py for the one-time migration
of any rows it held. This module is both the write path (used by the
admin-gated pipeline run in app.py and the CLI) and the read path used
by the public Matchday page (fetch_latest_predictions) and the admin
gate check (fetch_gate_status). RLS policies restrict the anon
(publishable) key to INSERT + SELECT on predictions/settlements (never
UPDATE or DELETE — a prediction can't be silently altered after the
fact) and SELECT only on gate_status (the app never writes it —
approving a league is a manual Supabase-side operation). Settling a
fixture's actual result is a separate append-only insert into
`settlements`, never a mutation of the `predictions` row.

Schema + RLS policies to run once in the Supabase SQL editor: see
supabase/schema.sql (fresh project) or
supabase/migrations/0002_phase0_revised.sql (existing project) at the
repo root — this module has no DDL access with only the anon/publishable
key.

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

from src.validation.gate_harness import get_code_version

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


def _resolve_app_secret() -> str | None:
    """SUPABASE_APP_SECRET, sent as the x-app-secret request header on
    every call this client makes. The predictions/settlements INSERT
    RLS policies (supabase/migrations/0003_indexes_and_header_secret.sql)
    require this header to match — the anon/publishable key alone is no
    longer sufficient to write a row, without ever using the
    service-role key. Returns None (not an error) if unconfigured; a
    client built without it can still read everything and write
    against the pre-migration policy, so this degrades gracefully
    during the rollout window rather than blocking client construction."""
    secret = os.environ.get("SUPABASE_APP_SECRET")
    if secret:
        return secret
    try:
        import streamlit as st
        return st.secrets.get("SUPABASE_APP_SECRET")
    except Exception:
        return None


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
        from supabase import ClientOptions, create_client
        app_secret = _resolve_app_secret()
        options = ClientOptions(headers={"x-app-secret": app_secret}) if app_secret else ClientOptions()
        return create_client(url, key, options=options)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not construct Supabase client: %s", exc)
        return None


def prediction_row_from_pipeline(
    row: pd.Series, run_id: str, model, settings: dict, price_source: str,
) -> dict[str, Any]:
    """Map one src.pipeline.build_predictions() output row (plus the
    fitted model it came from) onto the Supabase `predictions` schema.

    close_home/close_draw/close_away/close_source/retail_draw/
    actual_home_goals/actual_away_goals/actual_outcome_idx are always
    NULL here — the live app has no closing-price re-fetch and no
    result yet at prediction time; the real outcome lands in
    `settlements`, never backfilled onto this row (no UPDATE policy).
    matchweek is likewise NULL — that's a WP3 evaluation-harness concept
    (src.validation.gate_harness), not something the live pipeline
    computes today.
    """
    edge_cfg = (settings or {}).get("edge", {})
    return {
        "run_id": run_id,
        "model_version": MODEL_VERSION,
        "git_commit": get_code_version(),
        "fixture_id": row["match_id"],
        "match_date": pd.Timestamp(row["date"]).isoformat(),
        "league": row["league"],
        "season": None,
        "matchweek": None,
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
        "fallback_used": bool(model.fallback_used_) if model is not None else None,
        "skip_reason": "fallback_to_independent_poisson" if (model is not None and model.fallback_used_) else None,
        # Legacy single-price fields (Phase 0), still populated.
        "odds_entry": float(row["odds_draw"]),
        "price_source": price_source,
        "ev_entry": float(row["ev"]),
        # Full three-way entry price (WP3 schema parity).
        "entry_home": float(row["odds_home"]) if pd.notna(row.get("odds_home")) else None,
        "entry_draw": float(row["odds_draw"]),
        "entry_away": float(row["odds_away"]) if pd.notna(row.get("odds_away")) else None,
        "entry_source": price_source,
        "close_home": None, "close_draw": None, "close_away": None, "close_source": None,
        "retail_draw": None,
        "market_p_home": float(row["market_p_home"]) if pd.notna(row.get("market_p_home")) else None,
        "market_p_draw": float(row["market_p_draw"]),
        "market_p_away": float(row["market_p_away"]) if pd.notna(row.get("market_p_away")) else None,
        "actual_home_goals": None, "actual_away_goals": None, "actual_outcome_idx": None,
        "stake_shadow": float(row["stake_pct"]) if pd.notna(row.get("stake_pct")) else None,
        "bankroll_at_slate": float(edge_cfg.get("shadow_bankroll_units", 1.0)),
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
    fixture_id: str,
    home_goals: int,
    away_goals: int,
    actual_result: str,
    closing_odds_draw: float | None = None,
    close_source: str | None = None,
    settled_by: str | None = None,
    client=None,
) -> bool:
    """Append one settlement row. Returns True on success, False (logged
    warning) on any failure — never raises. Never touches `predictions`
    — settling a fixture is purely an insert into this table, joined
    back to its prediction by fixture_id at read time."""
    client = client or get_supabase_client()
    if client is None:
        return False

    payload = {
        "fixture_id": fixture_id,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "closing_odds_draw": closing_odds_draw,
        "close_source": close_source,
        "actual_result": actual_result,
        "settled_by": settled_by,
    }
    try:
        client.table("settlements").insert(payload).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase settlement write failed for %s: %s", fixture_id, exc)
        return False


def is_ledger_online(client=None) -> bool:
    """Cheap reachability probe (a 1-row, 1-column SELECT against the
    small gate_status table) — used to grey out every write-triggering
    admin control (Run pipeline, Record outcome) rather than let them
    fail after the fact. Never raises; a missing client or any query
    failure both mean "offline"."""
    client = client or get_supabase_client()
    if client is None:
        return False
    try:
        client.table("gate_status").select("league_code").limit(1).execute()
        return True
    except Exception:  # noqa: BLE001
        return False


def fetch_predictions(client=None, limit: int = 500) -> pd.DataFrame:
    """Read the `limit` most recently created prediction rows, across
    every run — the admin Ledger page's audit-trail source. Returns an
    empty DataFrame (logged warning, never raises) if the client is
    unavailable or the query fails, so a ledger outage degrades the
    page to "no entries yet" rather than a crash."""
    client = client or get_supabase_client()
    if client is None:
        return pd.DataFrame()

    try:
        resp = client.table("predictions").select("*").order("created_at", desc=True).limit(limit).execute()
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase predictions read failed: %s", exc)
        return pd.DataFrame()

    return pd.DataFrame(rows)


# Columns app.py's Matchday page actually reads (public cards plus the
# admin-only shadow-stake expander, which reads from this same frame —
# stake_shadow/ev_entry are gated by is_admin() in the UI, not excluded
# here). Narrower than "*", which pulled all ~40 columns including
# several (git_commit, converged, xi_val, close_*, ...) this page never
# touches.
_LATEST_PREDICTIONS_COLUMNS = (
    "fixture_id,run_id,match_date,league,home_id,away_id,"
    "p_home,p_draw,p_away,lambda_val,mu_val,rho_val,"
    "entry_source,stake_shadow,ev_entry,created_at"
)


def fetch_latest_predictions(client=None, limit: int = 1000) -> pd.DataFrame:
    """Read only the most recent pipeline run's predictions — the
    public Matchday page's sole data source; it never fits a model or
    calls a data feed itself.

    Two queries instead of fetch_predictions' fetch-500-then-filter:
    first the single most recent run_id (a 1-row, 1-column lookup),
    then only that run's rows, selecting only the columns app.py
    actually uses rather than every column in the table.
    """
    client = client or get_supabase_client()
    if client is None:
        return pd.DataFrame()

    try:
        latest = client.table("predictions").select("run_id").order("created_at", desc=True).limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase latest-run lookup failed: %s", exc)
        return pd.DataFrame()

    latest_rows = latest.data or []
    if not latest_rows:
        return pd.DataFrame()
    latest_run_id = latest_rows[0]["run_id"]

    try:
        resp = (
            client.table("predictions")
            .select(_LATEST_PREDICTIONS_COLUMNS)
            .eq("run_id", latest_run_id)
            .limit(limit)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase latest predictions read failed: %s", exc)
        return pd.DataFrame()

    return pd.DataFrame(rows)


def fetch_settlements(client=None, limit: int = 500) -> pd.DataFrame:
    """Read the `limit` most recently settled rows from `settlements`.
    Returns an empty DataFrame (logged warning, never raises) if the
    client is unavailable or the query fails.

    A fixture can have more than one settlement row if an earlier one
    was recorded in error — settlements is append-only, so a correction
    is a new row, never an edit of the old one. Callers that need "is
    this fixture settled" or "what's the current result" should use
    latest_settlement_by_fixture() below rather than assume one row per
    fixture_id here.
    """
    client = client or get_supabase_client()
    if client is None:
        return pd.DataFrame()

    try:
        resp = client.table("settlements").select("*").order("settled_at", desc=True).limit(limit).execute()
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase settlements read failed: %s", exc)
        return pd.DataFrame()

    return pd.DataFrame(rows)


def latest_settlement_by_fixture(client=None, limit: int = 500) -> pd.DataFrame:
    """One row per fixture_id — the most recently settled_at row for
    each, so a corrective re-settlement supersedes the one it corrects.
    Empty DataFrame if there are no settlements or the read fails."""
    df = fetch_settlements(client=client, limit=limit)
    if df.empty:
        return df
    df = df.sort_values("settled_at", ascending=False)
    return df.drop_duplicates(subset="fixture_id", keep="first").reset_index(drop=True)


def fetch_gate_status(client=None) -> dict[str, dict[str, Any]]:
    """Read the per-league staking-eligibility gate. Returns {} (logged
    warning, never raises) if the client is unavailable or the query
    fails — callers should treat a missing/failed read the same as "no
    league is eligible" (the safe default), not crash the admin page."""
    client = client or get_supabase_client()
    if client is None:
        return {}

    try:
        resp = client.table("gate_status").select("*").execute()
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase gate_status read failed: %s", exc)
        return {}

    return {row["league_code"]: row for row in rows}
