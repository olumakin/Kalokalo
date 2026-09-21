"""
Centralized, validated configuration management (IMP04).
Loads and validates settings.yaml using Pydantic schemas.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
import yaml

DEFAULT_CONFIG_PATH = Path("config/settings.yaml")


class DataSourceConfig(BaseModel):
    historical_base_url: str
    cache_dir: str = "data/historical"
    fixtures_dir: str = "data/fixtures"


class ModelConfig(BaseModel):
    rolling_window_days: int = Field(default=730, gt=30)
    min_matches_for_team_rating: int = Field(default=15, ge=1)
    xi_decay: float = Field(default=0.0065, gt=0.0, lt=0.1)
    xi_grid: list[float] = Field(default_factory=lambda: [0.003, 0.004, 0.005, 0.0065, 0.008, 0.010])
    goal_grid_size: int = Field(default=10, ge=5, le=30)
    max_optimizer_iterations: int = Field(default=500, ge=10)
    optimizer_method: str = "L-BFGS-B"
    rho_init: float = Field(default=-0.05, ge=-0.3, le=0.3)


class DevigConfig(BaseModel):
    method: Literal["multiplicative", "shin"] = "multiplicative"


class EdgeConfig(BaseModel):
    min_ev: float = Field(default=0.03, ge=0.0, le=0.5)
    kelly_fraction: float = Field(default=0.15, gt=0.0, le=1.0)
    single_match_cap: float = Field(default=0.025, gt=0.0, le=0.1)
    daily_slate_cap: float = Field(default=0.08, gt=0.0, le=0.5)

    @field_validator("daily_slate_cap")
    @classmethod
    def validate_caps(cls, v: float, info) -> float:
        single = info.data.get("single_match_cap", 0.025)
        if v < single:
            raise ValueError(f"daily_slate_cap ({v}) cannot be less than single_match_cap ({single})")
        return v


class LedgerConfig(BaseModel):
    path: str = "data/ledger.parquet"
    format: Literal["parquet", "csv"] = "parquet"


class ExecutionConfig(BaseModel):
    execution_price_source: str = "NAMED_OBSERVABLE_BOOKMAKER"
    benchmark_price_role: str = "REFERENCE_INFORMATION_FILTER_ONLY"
    allow_benchmark_fallback: bool = False
    max_execution_quote_age_seconds: int = Field(default=900, ge=10, le=86400)
    decision_lead_time_seconds: int = Field(default=3600, ge=60, le=86400)
    market_type: str = "1X2"
    selection: str = "DRAW"
    execution_mode: str = "PAPER_AT_OBSERVED_NAMED_QUOTE"
    market_relative_filter_policy: str = "MODEL_DRAW_PROB_EXCEEDS_BENCHMARK_DEVIGGED"


class AppConfig(BaseModel):
    leagues: dict[str, str]
    data_source: DataSourceConfig
    model: ModelConfig
    devig: DevigConfig
    edge: EdgeConfig
    ledger: LedgerConfig
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


_RESOLVED_CONFIG: AppConfig | None = None


def get_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load, validate, and cache application configuration once (IMP04)."""
    global _RESOLVED_CONFIG
    if _RESOLVED_CONFIG is not None and str(path) == str(DEFAULT_CONFIG_PATH):
        return _RESOLVED_CONFIG

    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"Configuration file not found: {target}")

    with open(target, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f)

    config = AppConfig.model_validate(raw_data)
    if str(path) == str(DEFAULT_CONFIG_PATH):
        _RESOLVED_CONFIG = config
    return config
