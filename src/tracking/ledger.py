"""
Persistent, append-only prediction ledger (PID Milestone 5 / Phase 4).

Every prediction the pipeline generates is recorded here, including ones
that don't qualify as bets, so model calibration can be audited against
actual outcomes and market movement after the fact.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

SCHEMA = [
    "timestamp", "match_id", "league", "home_team", "away_team",
    "model_p_draw", "market_p_draw", "odds_draw", "ev", "stake_pct",
    "actual_score", "clv", "pnl",
]


class Ledger:
    def __init__(self, path: str | Path = "data/ledger.parquet", fmt: str | None = None):
        self.path = Path(path)
        self.fmt = fmt or ("parquet" if self.path.suffix == ".parquet" else "csv")

    def _read(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=SCHEMA)
        if self.fmt == "parquet":
            return pd.read_parquet(self.path)
        return pd.read_csv(self.path)

    def _write(self, df: pd.DataFrame) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.fmt == "parquet":
            df.to_parquet(self.path, index=False)
        else:
            df.to_csv(self.path, index=False)

    def record_prediction(
        self,
        match_id: str,
        league: str,
        home_team: str,
        away_team: str,
        model_p_draw: float,
        market_p_draw: float,
        odds_draw: float,
        ev: float,
        stake_pct: float,
        timestamp: pd.Timestamp | None = None,
    ) -> None:
        """Append a new prediction row. actual_score/clv/pnl start unset
        and are filled in later via `update_outcome`."""
        df = self._read()
        row = {
            "timestamp": timestamp or pd.Timestamp.utcnow(),
            "match_id": match_id,
            "league": league,
            "home_team": home_team,
            "away_team": away_team,
            "model_p_draw": model_p_draw,
            "market_p_draw": market_p_draw,
            "odds_draw": odds_draw,
            "ev": ev,
            "stake_pct": stake_pct,
            "actual_score": None,
            "clv": None,
            "pnl": None,
        }
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        self._write(df)

    def update_outcome(
        self,
        match_id: str,
        actual_score: str,
        clv: float | None = None,
        pnl: float | None = None,
    ) -> None:
        """Backfill the result of a previously recorded prediction."""
        df = self._read()
        mask = df["match_id"] == match_id
        if not mask.any():
            raise KeyError(f"No ledger entry for match_id={match_id!r}")
        df.loc[mask, "actual_score"] = actual_score
        if clv is not None:
            df.loc[mask, "clv"] = clv
        if pnl is not None:
            df.loc[mask, "pnl"] = pnl
        self._write(df)

    def load(self) -> pd.DataFrame:
        return self._read()
