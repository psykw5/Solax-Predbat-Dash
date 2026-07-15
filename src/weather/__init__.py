"""Weather and solar forecast acquisition foundations for Wattson."""

from __future__ import annotations

import sys
from pathlib import Path

src_path = Path(__file__).resolve().parents[1]
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from .config import WeatherConfig, load_weather_config  # noqa: E402
from .evaluation import evaluate_forecasts_by_lead_time  # noqa: E402
from .pipeline import (  # noqa: E402
    build_evaluation_dataset,
    build_pvgis_baseline,
    collect_forecast,
    collect_historical_forecast_backfill,
    collect_observations,
    collect_satellite_radiation,
)

__all__ = [
    "WeatherConfig",
    "build_evaluation_dataset",
    "build_pvgis_baseline",
    "collect_forecast",
    "collect_historical_forecast_backfill",
    "collect_observations",
    "collect_satellite_radiation",
    "evaluate_forecasts_by_lead_time",
    "load_weather_config",
]
