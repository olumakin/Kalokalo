"""
Export the High Draw Likelihood cohort to CSV -- settled fixtures with
p_draw >= HIGH_TIE_P_DRAW, forecast next to the actual result. Not the
same scope as scripts/export_ledger.py (every match, any probability) --
this is the specific cohort the Matchday page tags "HIGH DRAW
LIKELIHOOD" / "HIGH TIE POTENTIAL".

    python -m scripts.export_high_draw_likelihood [--since 2026-08-01] [--out high_draw_likelihood.csv]
"""
from __future__ import annotations

import argparse

import pandas as pd

from src.ingestion.historical import current_season_start
from src.tracking.supabase_ledger import fetch_predictions_since, high_draw_likelihood_results, latest_settlement_by_fixture

# Must match app.py's HIGH_TIE_P_DRAW -- the same threshold driving the
# "HIGH DRAW LIKELIHOOD" / "HIGH TIE POTENTIAL" badge on the Matchday page.
HIGH_TIE_P_DRAW = 0.275


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the High Draw Likelihood cohort vs. actual results")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD; defaults to this season's start (Aug 1)")
    parser.add_argument("--out", default="high_draw_likelihood.csv")
    args = parser.parse_args()

    since = pd.Timestamp(args.since, tz="UTC") if args.since else current_season_start()
    predictions = fetch_predictions_since(since)
    settlements = latest_settlement_by_fixture(limit=5000)
    export = high_draw_likelihood_results(predictions, settlements, HIGH_TIE_P_DRAW, since=since)

    export.to_csv(args.out, index=False)
    print(f"Wrote {len(export)} settled High Draw Likelihood fixture(s) (p_draw >= {HIGH_TIE_P_DRAW}, since {since.date()}) to {args.out}")


if __name__ == "__main__":
    main()
