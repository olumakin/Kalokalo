"""
WP3: run the gate-evaluation harness against real historical data and
produce the go/no-go report. Composes existing, already-tested modules
(src.validation.gate_harness/clv/three_way/calibration/gate_report,
src.ingestion.historical/normalizer/data_loader) -- no changes to any
ingestion or app code, and the Dixon-Coles fit itself is untouched.

By default this now also applies gate_harness's walk-forward draw-
probability recalibration (--calibrate, on by default) on top of the
raw model output, refit at the same cadence as the model using only
strictly-prior resolved outcomes -- added after the first real WP3 run
showed every league's draw probabilities were meaningfully overconfident
(Cox slope 0.39-0.81). Pass --no-calibrate to reproduce that original,
uncorrected baseline for comparison. The report's calibration table
shows both the raw ("before") and corrected ("after") slope/intercept
so the fix's effect is visible directly, not just asserted.

Usage:
    python -m scripts.run_gate_evaluation --seasons-back 6

Expected runtime: a walk-forward retrain happens roughly every
`--retrain-every-days` (default 7) of in-season history, per league --
with 6 seasons across 5 leagues that's on the order of several hundred
individual model fits. Each single-league fit is much cheaper than the
multi-league joint fit used by the live admin pipeline (fewer teams,
less data per window), but this will still likely run for a while on a
full corpus. Progress prints to stdout as each league retrains so it's
visible this is working, not hung. Fits are cached to --cache-dir, so a
second run over the same corpus/code version (e.g. re-running with a
different --min-ev after reviewing results) reuses them instantly.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.ingestion.data_loader import select_entry_close_odds, validate_matches  # noqa: E402
from src.ingestion.historical import load_all, load_settings, season_codes  # noqa: E402
from src.ingestion.normalizer import CANONICAL_COLUMNS, load_team_mappings, resolve_team  # noqa: E402
from src.ingestion.data_loader import select_match_odds  # noqa: E402
from src.validation.calibration import calibration_slope_intercept  # noqa: E402
from src.validation.clv import evaluate_league_clv  # noqa: E402
from src.validation.gate_harness import (  # noqa: E402
    DEFAULT_CALIBRATION_MIN_RESOLVED,
    get_code_version,
    run_gate_evaluation,
)
from src.validation.gate_report import compute_gate_decision, render_gate_report_markdown  # noqa: E402
from src.validation.three_way import rps, three_way_log_loss  # noqa: E402

PRICING_COLUMNS = [
    "entry_home", "entry_draw", "entry_away", "entry_source",
    "close_home", "close_draw", "close_away", "close_source", "retail_draw",
]


def _normalize_with_pricing(raw: pd.DataFrame, mappings: dict) -> pd.DataFrame:
    """Same row-filtering/column logic as src.ingestion.normalizer
    .normalize_dataframe, but also carries the entry/close pricing
    columns through -- normalize_dataframe's own final column selection
    (CANONICAL_COLUMNS only) would drop them, and they're needed here
    for CLV evaluation. Reuses the same helpers normalize_dataframe
    itself calls (resolve_team, select_match_odds, select_entry_close_
    odds) rather than reimplementing any matching/pricing logic -- this
    only re-does the top-level row filtering and column assembly.
    """
    if raw.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS + PRICING_COLUMNS)

    df = raw.copy()
    df["date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    if df.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS + PRICING_COLUMNS)

    df["home_team"] = df.apply(lambda r: resolve_team(r["HomeTeam"], r["league"], mappings), axis=1)
    df["away_team"] = df.apply(lambda r: resolve_team(r["AwayTeam"], r["league"], mappings), axis=1)
    df["home_goals"] = df["FTHG"].astype(int)
    df["away_goals"] = df["FTAG"].astype(int)
    df["result"] = df["FTR"] if "FTR" in df.columns else df.apply(
        lambda r: "H" if r["home_goals"] > r["away_goals"] else ("A" if r["home_goals"] < r["away_goals"] else "D"),
        axis=1,
    )

    odds = df.apply(select_match_odds, axis=1, result_type="expand")
    odds.columns = ["odds_home", "odds_draw", "odds_away", "price_source"]
    df[["odds_home", "odds_draw", "odds_away", "price_source"]] = odds

    pricing = df.apply(select_entry_close_odds, axis=1, result_type="expand")
    df[PRICING_COLUMNS] = pricing[PRICING_COLUMNS]

    out = df[CANONICAL_COLUMNS + PRICING_COLUMNS].sort_values("date").reset_index(drop=True)
    out["date"] = out["date"].dt.tz_localize("UTC")
    return out


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="WP3 gate-evaluation harness runner")
    parser.add_argument("--leagues", default=",".join(settings["leagues"].keys()))
    parser.add_argument("--seasons-back", type=int, default=6)
    parser.add_argument("--xi", type=float, default=settings["model"]["xi_decay"],
                         help="Applied uniformly across leagues -- the xi grid search (model.xi_grid "
                              "in settings.yaml) isn't wired into this harness yet; see README Status.")
    parser.add_argument("--min-ev", type=float, default=settings["edge"]["min_ev"])
    parser.add_argument("--rolling-window-days", type=int, default=settings["model"]["rolling_window_days"])
    parser.add_argument("--retrain-every-days", type=int, default=7)
    parser.add_argument("--min-train-matches", type=int, default=200)
    parser.add_argument("--min-matches-per-team", type=int, default=settings["model"]["min_matches_for_team_rating"])
    parser.add_argument("--devig-method", default=settings["devig"]["method"])
    parser.add_argument("--cache-dir", default="data/cache/gate_eval")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument(
        "--calibrate", dest="calibrate", action="store_true", default=True,
        help="Walk-forward draw-probability recalibration on top of the raw model output "
             "(default: on). Refit at the same cadence as the model, using only strictly-prior "
             "resolved outcomes -- see gate_harness.run_league_evaluation docstring.",
    )
    parser.add_argument("--no-calibrate", dest="calibrate", action="store_false",
                         help="Disable recalibration -- reproduces the pre-fix WP3 baseline run.")
    parser.add_argument("--calibration-min-resolved", type=int, default=DEFAULT_CALIBRATION_MIN_RESOLVED)
    args = parser.parse_args()

    leagues = args.leagues.split(",")
    end_year = pd.Timestamp.now().year
    seasons = season_codes(end_year - args.seasons_back, end_year)
    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "data" / "gate_reports" / pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Code version: {get_code_version()}")
    print(f"Leagues: {leagues}  Seasons: {seasons[0]}..{seasons[-1]} ({len(seasons)} seasons)")
    print(f"xi={args.xi} (uniform -- see --xi help), min_ev={args.min_ev}, "
          f"rolling_window_days={args.rolling_window_days}, retrain_every_days={args.retrain_every_days}")
    print(f"calibrate={args.calibrate} (calibration_min_resolved={args.calibration_min_resolved})")
    print("Downloading/loading historical data...")

    t0 = time.time()
    raw = load_all(leagues, seasons, cache_dir=settings["data_source"]["cache_dir"])
    print(f"  {len(raw)} raw rows loaded in {time.time() - t0:.1f}s")

    mappings = load_team_mappings()
    matches = _normalize_with_pricing(raw, mappings)
    matches, val_report = validate_matches(matches)
    print(
        f"WP1 validation: {val_report['output_rows']}/{val_report['input_rows']} rows kept "
        f"(dropped {val_report['dropped_missing_core']} invalid core, "
        f"{val_report['dropped_same_team']} same-team, {val_report['dropped_duplicate']} duplicate; "
        f"{val_report['odds_invalidated']} implausible odds cleared, "
        f"{val_report['odds_coverage']} rows with a usable market price)"
    )
    priced = matches[matches["entry_draw"].notna() & matches["close_draw"].notna()]
    print(f"Entry+close price coverage: {len(priced)}/{len(matches)} rows "
          f"({len(priced) / max(len(matches), 1):.1%}) -- CLV can only be evaluated on these.")

    def on_retrain(current_date, train_df):
        print(f"  retrain at {current_date.date()} ({len(train_df)} matches in window)")

    xi_by_league = {lg: args.xi for lg in leagues}
    print("\nRunning walk-forward evaluation (this is the slow part)...")
    t0 = time.time()
    results, exclusions = run_gate_evaluation(
        matches, xi_by_league,
        min_train_matches=args.min_train_matches,
        min_matches_per_team=args.min_matches_per_team,
        rolling_window_days=args.rolling_window_days,
        retrain_every_days=args.retrain_every_days,
        devig_method=args.devig_method,
        min_ev=args.min_ev,
        cache_dir=args.cache_dir,
        on_retrain=on_retrain,
        calibrate=args.calibrate,
        calibration_min_resolved=args.calibration_min_resolved,
    )
    print(f"Evaluation finished in {time.time() - t0:.1f}s. "
          f"{len(results)} scored fixtures, {len(exclusions)} excluded.")

    clv_by_league, three_way_by_league, calibration_by_league = {}, {}, {}
    for league in leagues:
        league_results = results[results["league"] == league]
        if league_results.empty:
            continue
        clv_by_league[league] = evaluate_league_clv(
            league_results, entry_col="entry_draw", close_col="close_draw",
            flagged_mask=league_results["qualified"], block_col="matchweek",
        )
        scored = league_results.dropna(subset=["actual_outcome_idx"])
        if not scored.empty:
            three_way_by_league[league] = {
                "log_loss": three_way_log_loss(
                    scored["actual_outcome_idx"], scored["p_home"], scored["p_draw"], scored["p_away"],
                ),
                "rps": rps(
                    scored["actual_outcome_idx"], scored["p_home"], scored["p_draw"], scored["p_away"],
                ),
            }
            is_draw = (scored["actual_outcome_idx"] == 1).to_numpy()
            a_after, b_after = calibration_slope_intercept(is_draw, scored["p_draw"].to_numpy())
            a_before, b_before = calibration_slope_intercept(is_draw, scored["p_draw_raw"].to_numpy())
            calibration_by_league[league] = {
                "intercept": a_after, "slope": b_after,
                "intercept_before": a_before, "slope_before": b_before,
                "n_calibrated": int(scored["calibration_applied"].sum()),
            }

    decision = compute_gate_decision(clv_by_league)
    report_md = render_gate_report_markdown(clv_by_league, decision, exclusions, three_way_by_league)

    if calibration_by_league:
        report_md += "\n## Draw-probability calibration (Cox regression)\n\n"
        report_md += "Well-calibrated: intercept ~0, slope ~1. Slope < 1 = overconfident.\n"
        report_md += (
            f"Recalibration: {'ON' if args.calibrate else 'OFF'} "
            f"(min_resolved={args.calibration_min_resolved}). "
            "'Before' is the raw Dixon-Coles output; 'After' is what was actually used for "
            "EV/qualification/CLV in this run.\n\n"
        )
        report_md += (
            "| League | Intercept (before) | Slope (before) | Intercept (after) | Slope (after) | "
            "Fixtures calibrated |\n|---|---|---|---|---|---|\n"
        )
        for league in sorted(calibration_by_league):
            c = calibration_by_league[league]
            report_md += (
                f"| {league} | {c['intercept_before']:.3f} | {c['slope_before']:.3f} | "
                f"{c['intercept']:.3f} | {c['slope']:.3f} | {c['n_calibrated']} |\n"
            )

    results.to_csv(out_dir / "results.csv", index=False)
    exclusions.to_csv(out_dir / "exclusions.csv", index=False)
    (out_dir / "gate_report.md").write_text(report_md)

    print(f"\nWrote results.csv, exclusions.csv, gate_report.md to {out_dir}\n")
    print("=" * 70)
    print(report_md)
    print("=" * 70)


if __name__ == "__main__":
    main()
