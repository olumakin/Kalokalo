"""
R02: Automated evidence pack orchestrator for Big 5 historical evaluation.

Executes walk-forward evaluations across all configured leagues, aggregates
CLV, RPS, log-loss, calibration, exclusions, and gate decisions, and outputs both
human-readable markdown and machine-readable JSON artifacts.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from src.validation.clv import evaluate_league_clv
from src.validation.gate_harness import assert_datetime_utc, get_code_version, run_league_evaluation
from src.validation.gate_report import compute_gate_decision, render_gate_report_markdown
from src.validation.three_way import rps, three_way_log_loss

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_evidence_pack(
    matches: pd.DataFrame,
    leagues: list[str],
    xi_by_league: dict[str, float] | None = None,
    min_train_matches: int = 200,
    retrain_every_days: int = 7,
    devig_method: str = "multiplicative",
    min_ev: float = 0.03,
    cache_dir: Path | str | None = "data/cache/models",
    output_report_path: Path | str = "docs/reports/gate_decision_initial.md",
    output_json_path: Path | str = "data/reports/gate_decision_initial.json",
    run_id: str = "initial_corpus_run",
) -> dict[str, Any]:
    """Execute end-to-end evidence pack across leagues and compile gate reports (R02).

    Parameters
    ----------
    matches : pd.DataFrame
        Historical canonical dataframe containing both entry and close pricing legs.
    leagues : list[str]
        List of league codes (e.g. ["E0", "SP1", "I1", "D1", "F1"]).
    xi_by_league : dict[str, float], optional
        Per-league optimal decay rate xi. Defaults to 0.0065 if omitted.
    """
    assert_datetime_utc(matches, "date")
    xi_by_league = xi_by_league or {}
    code_version = get_code_version()

    clv_by_league: dict[str, dict] = {}
    three_way_by_league: dict[str, dict] = {}
    all_exclusions: list[pd.DataFrame] = []
    league_summaries: dict[str, dict] = {}

    for league in leagues:
        xi = xi_by_league.get(league, 0.0065)
        logger.info("Running evidence evaluation for %s (xi=%.4f)", league, xi)

        results, exclusions = run_league_evaluation(
            matches=matches,
            league=league,
            xi=xi,
            min_train_matches=min_train_matches,
            retrain_every_days=retrain_every_days,
            devig_method=devig_method,
            min_ev=min_ev,
            cache_dir=cache_dir,
            code_version=code_version,
            run_id=f"{run_id}_{league}",
        )

        all_exclusions.append(exclusions)

        if results.empty:
            logger.warning("No evaluation results returned for league %s", league)
            clv_by_league[league] = {
                "baseline": {"n": 0, "mean_clv": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan"), "insufficient_sample": True},
                "flagged": {"n": 0, "mean_clv": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan"), "insufficient_sample": True},
            }
            continue

        # Evaluate 2-price CLV against sharp Pinnacle closing lines
        clv = evaluate_league_clv(
            df=results,
            entry_col="entry_draw",
            close_col="close_draw",
            flagged_mask=results["qualified"],
            block_col="matchweek",
        )
        clv_by_league[league] = clv

        # Evaluate three-way probabilistic forecasting performance
        outcome_idx = results["actual_outcome_idx"]
        p_h, p_d, p_a = results["p_home"], results["p_draw"], results["p_away"]

        ll = float(three_way_log_loss(outcome_idx, p_h, p_d, p_a))
        r = float(rps(outcome_idx, p_h, p_d, p_a))
        three_way_by_league[league] = {"log_loss": round(ll, 4), "rps": round(r, 4)}

        league_summaries[league] = {
            "eval_matches": len(results),
            "flagged_bets": int(results["qualified"].sum()),
            "converged_ratio": round(float(results["converged"].mean()), 4),
            "baseline_mean_clv": round(float(clv["baseline"]["mean_clv"]), 4) if pd.notna(clv["baseline"]["mean_clv"]) else None,
            "flagged_mean_clv": round(float(clv["flagged"]["mean_clv"]), 4) if pd.notna(clv["flagged"]["mean_clv"]) else None,
            "flagged_ci_lo": round(float(clv["flagged"]["ci_lo"]), 4) if pd.notna(clv["flagged"]["ci_lo"]) else None,
            "flagged_ci_hi": round(float(clv["flagged"]["ci_hi"]), 4) if pd.notna(clv["flagged"]["ci_hi"]) else None,
            "log_loss": round(ll, 4),
            "rps": round(r, 4),
        }

    combined_exclusions = pd.concat(all_exclusions, ignore_index=True) if all_exclusions else pd.DataFrame()
    decision = compute_gate_decision(clv_by_league)

    report_markdown = render_gate_report_markdown(
        clv_by_league=clv_by_league,
        decision=decision,
        exclusions=combined_exclusions,
        three_way_by_league=three_way_by_league,
    )

    # Enrich markdown with execution metadata
    metadata_header = (
        f"# Statistical Gate Evidence Pack\n\n"
        f"- **Run ID**: `{run_id}`\n"
        f"- **Code Version**: `{code_version}`\n"
        f"- **Leagues**: `{', '.join(leagues)}`\n"
        f"- **Evaluated Matches**: {sum(s.get('eval_matches', 0) for s in league_summaries.values())}\n"
        f"- **Total Exclusions**: {len(combined_exclusions)}\n\n"
    )
    full_markdown = metadata_header + report_markdown

    # Save markdown report
    report_file = Path(output_report_path)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(full_markdown, encoding="utf-8")
    logger.info("Saved gate report markdown to %s", report_file)

    # Save JSON metrics
    json_data = {
        "run_id": run_id,
        "code_version": code_version,
        "leagues": leagues,
        "xi_by_league": xi_by_league,
        "gate_decision": decision,
        "league_summaries": league_summaries,
        "exclusions_summary": combined_exclusions["skip_reason"].value_counts().to_dict() if not combined_exclusions.empty else {},
    }
    json_file = Path(output_json_path)
    json_file.parent.mkdir(parents=True, exist_ok=True)
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)
    logger.info("Saved gate report JSON to %s", json_file)

    return json_data
