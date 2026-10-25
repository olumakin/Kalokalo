"""
Export the prediction ledger to CSV, one row per match -- not the
Ledger page's download button, for anyone who wants the file without
going through the deployed app (needs the usual Supabase env vars set
locally, same as the rest of this codebase's Supabase access).

    python -m scripts.export_ledger [--since 2026-08-01] [--out ledger_export.csv]

A fixture gets re-predicted every time the pipeline runs while it's
still upcoming, so the raw ledger carries several rows per match (a
median of ~9, in practice) -- this keeps only the most recent forecast
per match and merges in the actual result where the fixture's been
settled, so the export is one row per match rather than mostly
duplicates.
"""
from __future__ import annotations

import argparse

import pandas as pd

from src.ingestion.historical import current_season_start
from src.tracking.supabase_ledger import fetch_predictions_since, latest_settlement_by_fixture, ledger_export


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the prediction ledger, one row per match")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD; defaults to this season's start (Aug 1)")
    parser.add_argument("--out", default="ledger_export.csv")
    args = parser.parse_args()

    since = pd.Timestamp(args.since, tz="UTC") if args.since else current_season_start()
    predictions = fetch_predictions_since(since)
    settlements = latest_settlement_by_fixture(limit=5000)
    export = ledger_export(predictions, settlements)

    export.to_csv(args.out, index=False)
    print(f"Wrote {len(export)} rows (one per match, since {since.date()}) to {args.out}")


if __name__ == "__main__":
    main()
