"""
Script to execute the complete historical evidence pack workflow (R01 & R02):
1. Assembles real historical matches with distinct entry/closing legs (A10).
2. Runs chronological xi sweep across leagues to determine optimal decay (R01).
3. Executes walk-forward evaluation across all 5 leagues producing gate decision (R02).
"""
import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.ingestion.historical import assemble_historical_evaluation_dataset
from src.validation.gate_pipeline import run_evidence_pack
from src.validation.xi_sweep import sweep_all_leagues

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("evidence_pack_runner")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Big 5 Historical Evidence Pack & Gate Decision")
    parser.add_argument("--leagues", nargs="+", default=["E0", "SP1", "I1", "D1", "F1"], help="Leagues to evaluate")
    parser.add_argument("--seasons", nargs="+", default=["2122", "2223", "2324", "2425"], help="Seasons to evaluate")
    parser.add_argument("--skip-sweep", action="store_true", help="Skip xi sweep and use default 0.0065")
    parser.add_argument("--cache-dir", default="data/cache/models", help="Model fit cache directory")
    parser.add_argument("--report-path", default="docs/reports/gate_decision_initial.md", help="Output markdown path")
    parser.add_argument("--json-path", default="data/reports/gate_decision_initial.json", help="Output JSON path")
    parser.add_argument("--manifest-path", default="data/reports/xi_sweep_manifest.json", help="Output sweep path")

    args = parser.parse_args()

    logger.info("Step 1: Assembling historical evaluation dataset for leagues=%s, seasons=%s", args.leagues, args.seasons)
    matches_df, data_manifest = assemble_historical_evaluation_dataset(
        leagues=args.leagues,
        seasons=args.seasons,
    )
    logger.info(
        "Assembled %d clean matches (missing entry: %d, missing close: %d, fully priced: %d)",
        len(matches_df),
        data_manifest["missing_price_report"]["missing_entry_count"],
        data_manifest["missing_price_report"]["missing_close_count"],
        data_manifest["missing_price_report"]["fully_priced_count"],
    )

    if matches_df.empty:
        logger.error("No matches assembled. Aborting.")
        return 1

    xi_by_league: dict[str, float] = {}
    if not args.skip_sweep:
        logger.info("Step 2: Executing chronological xi sweep (R01)...")
        sweep_res = sweep_all_leagues(
            matches=matches_df,
            leagues=args.leagues,
            xi_grid=[0.003, 0.005, 0.0065, 0.008, 0.010],
            split_ratio=0.70,
            output_manifest=args.manifest_path,
            min_train_matches=200,
            retrain_every_days=14,
            cache_dir=args.cache_dir,
        )
        for lg, res in sweep_res["leagues"].items():
            if res.get("selected_xi") is not None:
                xi_by_league[lg] = res["selected_xi"]
        logger.info("Optimal decay rates selected from sweep: %s", xi_by_league)
    else:
        logger.info("Skipping xi sweep; using 0.0065 for all leagues.")
        xi_by_league = {lg: 0.0065 for lg in args.leagues}

    logger.info("Step 3: Running walk-forward evidence pack evaluation (R02)...")
    results = run_evidence_pack(
        matches=matches_df,
        leagues=args.leagues,
        xi_by_league=xi_by_league,
        min_train_matches=200,
        retrain_every_days=14,
        cache_dir=args.cache_dir,
        output_report_path=args.report_path,
        output_json_path=args.json_path,
    )

    logger.info("Gate Decision: %s", results["gate_decision"]["decision"])
    logger.info("Gate Reason: %s", results["gate_decision"]["reason"])
    if results["gate_decision"]["leagues"]:
        logger.info("Leagues Cleared: %s", results["gate_decision"]["leagues"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
