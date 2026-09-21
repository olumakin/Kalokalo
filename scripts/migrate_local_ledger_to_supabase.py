"""
One-time migration: local file ledger -> Supabase.

Phase 0 (revised) retires the local ledger (src/tracking/ledger.py,
deleted) in favor of Supabase as the only ledger. If this deployment
ever ran with the local ledger active, its rows need moving across
once so history isn't silently lost. Run by hand, once:

    python -m scripts.migrate_local_ledger_to_supabase [--path data/ledger.parquet]

Deliberately does not import the deleted src.tracking.ledger.Ledger
class — reads the parquet/csv file directly with pandas instead, so
that module can stay fully gone from the live path.

The old ledger's schema (src/tracking/ledger.py, for reference — this
comment is the only place that schema is documented now):

    timestamp, match_id, league, home_team, away_team, model_p_draw,
    market_p_draw, odds_draw, ev, stake_pct, actual_score, clv, pnl

Every WP3-schema field the old ledger never captured (lambda/mu/rho,
matchweek, three-way entry/close prices, market_p_home/away, ...) is
written as NULL — that data genuinely doesn't exist for these old
rows; NULL is the correct, honest representation, not a bug to paper
over with a fabricated value.

A settled old row (actual_score populated) also gets a `settlements`
row, matching the new architecture's split (predictions never carries
an outcome; settlements does) — the closing draw price is
reverse-derived from the old ledger's `clv` field (clv = odds_draw /
closing_odds - 1) where both odds_draw and clv are present; otherwise
left NULL.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

import pandas as pd

from src.tracking.supabase_ledger import MODEL_VERSION, get_supabase_client, write_predictions, write_settlement


def _load_old_ledger(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _match_date_from_match_id(match_id: str) -> str | None:
    """match_id is built as f'{date}_{league}_{home}_{away}' (see the
    old src/pipeline.py:build_predictions) — the date is always the
    first '_'-delimited component and always YYYY-MM-DD, so this is a
    reliable (not a best-effort guess) extraction."""
    if not isinstance(match_id, str) or "_" not in match_id:
        return None
    date_part = match_id.split("_", 1)[0]
    try:
        return pd.Timestamp(date_part).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _predictions_row(row: pd.Series, run_id: str) -> dict:
    odds_draw = row.get("odds_draw")
    return {
        "run_id": run_id,
        "model_version": MODEL_VERSION,
        "git_commit": None,  # unknown for historical rows; not fabricated
        "fixture_id": row["match_id"],
        "match_date": _match_date_from_match_id(row["match_id"]),
        "league": row.get("league"),
        "season": None,
        "matchweek": None,
        "home_id": row.get("home_team"),
        "away_id": row.get("away_team"),
        "p_home": None,
        "p_draw": float(row["model_p_draw"]) if pd.notna(row.get("model_p_draw")) else None,
        "p_away": None,
        "lambda_val": None, "mu_val": None, "rho_val": None, "xi_val": None,
        "converged": None, "fallback_used": None, "skip_reason": None,
        "odds_entry": float(odds_draw) if pd.notna(odds_draw) else None,
        "price_source": "migrated_local_ledger",
        "ev_entry": float(row["ev"]) if pd.notna(row.get("ev")) else None,
        "entry_home": None,
        "entry_draw": float(odds_draw) if pd.notna(odds_draw) else None,
        "entry_away": None,
        "entry_source": "migrated_local_ledger",
        "close_home": None, "close_draw": None, "close_away": None, "close_source": None,
        "retail_draw": None,
        "market_p_home": None,
        "market_p_draw": float(row["market_p_draw"]) if pd.notna(row.get("market_p_draw")) else None,
        "market_p_away": None,
        "actual_home_goals": None, "actual_away_goals": None, "actual_outcome_idx": None,
        "stake_shadow": float(row["stake_pct"]) if pd.notna(row.get("stake_pct")) else None,
        "bankroll_at_slate": None,
    }


def _settlement_from_old_row(row: pd.Series) -> dict | None:
    actual_score = row.get("actual_score")
    if not isinstance(actual_score, str) or "-" not in actual_score:
        return None
    try:
        home_goals, away_goals = (int(x) for x in actual_score.split("-", 1))
    except ValueError:
        return None

    actual_result = "D" if home_goals == away_goals else ("H" if home_goals > away_goals else "A")

    closing_odds_draw = None
    odds_draw, clv = row.get("odds_draw"), row.get("clv")
    if pd.notna(odds_draw) and pd.notna(clv) and (1 + clv) != 0:
        closing_odds_draw = float(odds_draw) / (1 + float(clv))

    return {
        "fixture_id": row["match_id"],
        "home_goals": home_goals,
        "away_goals": away_goals,
        "actual_result": actual_result,
        "closing_odds_draw": closing_odds_draw,
        "close_source": "migrated_local_ledger" if closing_odds_draw is not None else None,
        "settled_by": "migration_script",
    }


def migrate(path: Path) -> dict:
    old = _load_old_ledger(path)
    report = {
        "rows_read": len(old),
        "predictions_written": 0,
        "predictions_failed": 0,
        "settlements_written": 0,
        "settlements_failed": 0,
        "rows_skipped_no_match_id": 0,
    }
    if old.empty:
        return report

    client = get_supabase_client()
    if client is None:
        print("ERROR: Supabase credentials not configured — nothing written.", file=sys.stderr)
        report["predictions_failed"] = len(old)
        return report

    valid = old[old["match_id"].notna()]
    report["rows_skipped_no_match_id"] = len(old) - len(valid)

    run_id = str(uuid.uuid4())
    pred_rows = [_predictions_row(row, run_id) for _, row in valid.iterrows()]
    failed = write_predictions(pred_rows, client=client)
    report["predictions_written"] = len(pred_rows) - failed
    report["predictions_failed"] = failed

    for _, row in valid.iterrows():
        settlement = _settlement_from_old_row(row)
        if settlement is None:
            continue
        ok = write_settlement(client=client, **settlement)
        if ok:
            report["settlements_written"] += 1
        else:
            report["settlements_failed"] += 1

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the retired local file ledger into Supabase, once.")
    parser.add_argument("--path", default="data/ledger.parquet", help="Path to the old ledger file (parquet or csv).")
    args = parser.parse_args()

    report = migrate(Path(args.path))

    print("Migration report:")
    for key, value in report.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
