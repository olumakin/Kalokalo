"""
WP3 gate-evaluation harness: strict walk-forward evaluation producing
the evidence pack a go/no-go decision is made from (src.validation
.gate_report.compute_gate_decision consumes this module's output).

Distinct from src.validation.backtest.WalkForwardBacktest (the existing
Streamlit Backtest page's simpler, single-price, single-xi harness,
left as-is so that page keeps working): this module is the more
rigorous per-league evaluation this repo did not previously have —
per-league xi, team-ID-keyed warm starts across season boundaries,
two-price CLV against a genuine entry/close pair, exclusion tracking
with reason codes, and disk-cached fits so re-running the report
doesn't refit identical work.

The harness never parses dates itself — `matches["date"]` must already
be datetime64 (UTC) from the ingestion layer (src.ingestion.data_loader
/ normalizer); it asserts this and raises rather than silently calling
pd.to_datetime, which would reintroduce day-first/timezone ambiguity
this far downstream.
"""
from __future__ import annotations

import hashlib
import logging
import pickle
import subprocess
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from src.analytics.devig import devig
from src.analytics.edge import calculate_ev, qualifies
from src.models.dixon_coles import DixonColesModel
from src.models.simulator import match_probabilities

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Exclusion reason codes.
REASON_INSUFFICIENT_LEAGUE_HISTORY = "insufficient_league_history"
REASON_UNSEEN_TEAM = "unseen_team"

EXCLUSION_COLUMNS = ["date", "season", "league", "fixture_id", "skip_reason"]

RESULTS_COLUMNS = [
    "run_id", "model_version", "date", "season", "league", "matchweek", "fixture_id",
    "home_team", "away_team", "xi_used",
    "lambda", "mu", "rho",
    "p_home", "p_draw", "p_away",
    "entry_home", "entry_draw", "entry_away", "entry_source",
    "close_home", "close_draw", "close_away", "close_source",
    "retail_draw",
    "market_p_home", "market_p_draw", "market_p_away",
    "ev", "qualified",
    "actual_home_goals", "actual_away_goals", "actual_outcome_idx",
    "converged", "fallback_used",
]


def get_code_version() -> str:
    """Short git commit hash of the running code, for the results
    schema's model_version field — a hard-coded version string can't
    tell you which fix a given evaluation run did or didn't include.
    Falls back to "unknown" (e.g. a packaged deploy with no .git dir)
    rather than raising: the harness's job is to evaluate the model,
    not to enforce a git checkout."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def assert_datetime_utc(df: pd.DataFrame, col: str = "date") -> None:
    """The harness never parses dates — that's the ingestion layer's
    job (src.ingestion.data_loader/normalizer). Re-parsing this far
    downstream reintroduces the day-first ambiguity WP1 exists to
    close if the column ever arrives as a plain string."""
    if col not in df.columns:
        raise ValueError(f"Missing required column {col!r}")
    dtype = df[col].dtype
    if not pd.api.types.is_datetime64_any_dtype(dtype):
        raise TypeError(
            f"harness requires {col!r} to already be datetime64 (got {dtype}); "
            "parse it in the ingestion layer, not here."
        )


def _default_fit_func(
    train_df: pd.DataFrame, xi: float, warm_start: dict[str, tuple[float, float]] | None,
    max_iter: int, min_matches: int, method: str,
) -> DixonColesModel:
    return DixonColesModel(min_matches=min_matches, max_iter=max_iter, method=method).fit(
        train_df, xi=xi, warm_start=warm_start,
    )


def _cache_key(league: str, matchweek_start: str, xi: float, code_version: str) -> str:
    raw = f"{league}|{matchweek_start}|{xi:.6f}|{code_version}"
    return hashlib.sha1(raw.encode()).hexdigest()  # noqa: S324 — cache key, not a security boundary


def _load_cached_model(cache_dir: Path | None, key: str) -> DixonColesModel | None:
    if cache_dir is None:
        return None
    path = Path(cache_dir) / f"{key}.pkl"
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)  # noqa: S301 — trusted, locally-written cache file
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load cached fit %s: %s", path, exc)
        return None


def _save_cached_model(cache_dir: Path | None, key: str, model: DixonColesModel) -> None:
    if cache_dir is None:
        return
    cache_dir = Path(cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_dir / f"{key}.pkl", "wb") as f:
            pickle.dump(model, f)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write cached fit under %s: %s", cache_dir, exc)


def run_league_evaluation(
    matches: pd.DataFrame,
    league: str,
    xi: float,
    fit_func: Callable = _default_fit_func,
    min_train_matches: int = 200,
    min_matches_per_team: int = 15,
    rolling_window_days: int = 730,
    retrain_every_days: int = 7,
    devig_method: str = "multiplicative",
    min_ev: float = 0.03,
    max_iter: int = 500,
    method: str = "L-BFGS-B",
    cache_dir: str | Path | None = None,
    code_version: str | None = None,
    run_id: str | None = None,
    on_retrain: Callable[[pd.Timestamp, pd.DataFrame], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Strict chronological walk-forward evaluation for one league.

    `on_retrain`, if given, is called as `on_retrain(current_date,
    train_df)` at every retrain *attempt* (whether or not it succeeds),
    before fit_func runs — a hook for progress reporting, and for tests
    to verify the no-leakage guarantee precisely (train_df's contents
    vs. current_date) without needing to independently reconstruct the
    retrain-trigger schedule, which depends on data availability as
    well as `retrain_every_days`.

    `matches` must already be priced (entry_home/draw/away, entry_source,
    close_home/draw/away, close_source, retail_draw — see
    src.ingestion.data_loader.select_entry_close_odds) and canonical
    (home_goals/away_goals/home_team/away_team/season), with `date`
    already datetime64 (UTC).

    Returns (results, exclusions). `min_train_matches` gates whether
    there's enough data in the *current rolling window* to retrain at
    all — the old "at least 500 matches in the whole league" gate is
    removed: with data from 2014/15 onward it was always satisfied, so
    it verified nothing (WP3 review item 14); the meaningful check is
    always "enough recent data to fit right now," which this already
    performs at every retrain checkpoint. A whole-league minimum would
    need a *per-team* history requirement to mean anything, and smooth
    shrinkage (WP2) already handles sparse teams continuously rather
    than needing a hard team-level cutoff.
    """
    assert_datetime_utc(matches, "date")
    code_version = code_version or get_code_version()
    run_id = run_id or "unassigned"

    df = matches[matches["league"] == league].sort_values("date").reset_index(drop=True)
    # Calendar-week bucket as a matchweek/round proxy (football-data.co.uk
    # doesn't carry an explicit round identifier) — only day-granularity
    # matters here, so drop tz before to_period rather than let pandas
    # warn about discarding it silently.
    df["matchweek"] = df["date"].dt.tz_localize(None).dt.to_period("W").astype(str)

    if df.empty:
        return pd.DataFrame(columns=RESULTS_COLUMNS), pd.DataFrame(columns=EXCLUSION_COLUMNS)

    unique_dates = sorted(df["date"].unique())

    model: DixonColesModel | None = None
    prev_team_params: dict[str, tuple[float, float]] | None = None
    last_train_date: pd.Timestamp | None = None
    results: list[dict] = []
    exclusions: list[dict] = []

    for current_date in unique_dates:
        needs_retrain = (
            model is None or last_train_date is None
            or (current_date - last_train_date).days >= retrain_every_days
        )
        if needs_retrain:
            window_start = current_date - pd.Timedelta(days=rolling_window_days)
            train_df = df[(df["date"] < current_date) & (df["date"] >= window_start)]
            if on_retrain is not None:
                on_retrain(current_date, train_df)

            if len(train_df) < min_train_matches:
                model = None
            else:
                key = _cache_key(league, str(current_date.date()), xi, code_version)
                cached = _load_cached_model(cache_dir, key)
                if cached is not None:
                    model = cached
                else:
                    try:
                        model = fit_func(
                            train_df, xi=xi, warm_start=prev_team_params,
                            max_iter=max_iter, min_matches=min_matches_per_team, method=method,
                        )
                    except ValueError as exc:
                        logger.warning("Skipping retrain for %s at %s: %s", league, current_date, exc)
                        model = None
                    if model is not None:
                        _save_cached_model(cache_dir, key, model)
                if model is not None:
                    prev_team_params = {t: (model.alpha_[t], model.beta_[t]) for t in model.teams_}
            last_train_date = current_date

        day_matches = df[df["date"] == current_date]
        for _, row in day_matches.iterrows():
            fixture_id = f"{row['date'].date()}_{league}_{row['home_team']}_{row['away_team']}"
            season = row.get("season")
            matchweek = row["matchweek"]

            if model is None:
                exclusions.append({
                    "date": row["date"], "season": season, "league": league,
                    "fixture_id": fixture_id, "skip_reason": REASON_INSUFFICIENT_LEAGUE_HISTORY,
                })
                continue

            if row["home_team"] not in model.teams_ or row["away_team"] not in model.teams_:
                exclusions.append({
                    "date": row["date"], "season": season, "league": league,
                    "fixture_id": fixture_id, "skip_reason": REASON_UNSEEN_TEAM,
                })
                continue

            lam, mu, rho = model.predict(row["home_team"], row["away_team"])
            probs = match_probabilities(lam, mu, rho)
            p_home, p_draw, p_away = probs["p_home"], probs["p_draw"], probs["p_away"]
            # build_score_matrix already clips and renormalizes onto the
            # simplex; this is a cheap sanity check, not a fix — a
            # regression there should be loud, not silently patched here.
            total = p_home + p_draw + p_away
            if not np.isclose(total, 1.0, atol=1e-6):
                p_home, p_draw, p_away = p_home / total, p_draw / total, p_away / total

            entry_h, entry_d, entry_a = row.get("entry_home"), row.get("entry_draw"), row.get("entry_away")
            if pd.notna(entry_h) and pd.notna(entry_d) and pd.notna(entry_a):
                market_h, market_d, market_a = devig(entry_h, entry_d, entry_a, method=devig_method)
                ev = calculate_ev(p_draw, entry_d)
                qualified = qualifies(p_draw, market_d, ev, min_ev=min_ev)
            else:
                market_h = market_d = market_a = float("nan")
                ev = float("nan")
                qualified = False

            actual_idx = 0 if row["home_goals"] > row["away_goals"] else (
                2 if row["home_goals"] < row["away_goals"] else 1
            )

            results.append({
                "run_id": run_id, "model_version": code_version,
                "date": row["date"], "season": season, "league": league,
                "matchweek": matchweek, "fixture_id": fixture_id,
                "home_team": row["home_team"], "away_team": row["away_team"], "xi_used": xi,
                "lambda": lam, "mu": mu, "rho": rho,
                "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
                "entry_home": entry_h, "entry_draw": entry_d, "entry_away": entry_a,
                "entry_source": row.get("entry_source"),
                "close_home": row.get("close_home"), "close_draw": row.get("close_draw"),
                "close_away": row.get("close_away"), "close_source": row.get("close_source"),
                "retail_draw": row.get("retail_draw"),
                "market_p_home": market_h, "market_p_draw": market_d, "market_p_away": market_a,
                "ev": ev, "qualified": qualified,
                "actual_home_goals": row["home_goals"], "actual_away_goals": row["away_goals"],
                "actual_outcome_idx": actual_idx,
                "converged": model.converged_, "fallback_used": model.fallback_used_,
            })

    results_df = pd.DataFrame(results, columns=RESULTS_COLUMNS) if results else pd.DataFrame(columns=RESULTS_COLUMNS)
    exclusions_df = (
        pd.DataFrame(exclusions, columns=EXCLUSION_COLUMNS) if exclusions else pd.DataFrame(columns=EXCLUSION_COLUMNS)
    )
    return results_df, exclusions_df


def run_gate_evaluation(
    matches: pd.DataFrame,
    xi_by_league: dict[str, float],
    run_id: str | None = None,
    **kwargs,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run run_league_evaluation for every league in `xi_by_league`
    (the WP2 per-league grid-search output), concatenating results and
    exclusions. Sequential, not parallelized across leagues — see
    gate_harness's module-level note on why (README documents the
    tradeoff); each league's fits are still cached to disk if
    `cache_dir` is passed through kwargs, so a repeated report over the
    same corpus/code version doesn't refit regardless.
    """
    import uuid
    run_id = run_id or str(uuid.uuid4())
    code_version = kwargs.pop("code_version", None) or get_code_version()

    all_results, all_exclusions = [], []
    for league, xi in xi_by_league.items():
        results, exclusions = run_league_evaluation(
            matches, league, xi, run_id=run_id, code_version=code_version, **kwargs,
        )
        all_results.append(results)
        all_exclusions.append(exclusions)

    results_df = pd.concat(all_results, ignore_index=True) if all_results else pd.DataFrame(columns=RESULTS_COLUMNS)
    exclusions_df = (
        pd.concat(all_exclusions, ignore_index=True) if all_exclusions else pd.DataFrame(columns=EXCLUSION_COLUMNS)
    )
    return results_df, exclusions_df
