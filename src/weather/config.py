"""Private weather configuration."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from config.loader import load_wattson_config
from utils.env import read_dotenv

DEFAULT_RAW_WEATHER_DIR = Path("data/raw/weather")
DEFAULT_PROCESSED_WEATHER_DIR = Path("data/processed/weather")
DEFAULT_WEATHER_DB_PATH = Path("data/live/wattson-weather.sqlite")

WEATHER_ENV_KEYS = ["WATTSON_LATITUDE", "WATTSON_LONGITUDE"]


class WeatherConfig(BaseModel):
    """Local-only solar installation and location configuration."""

    model_config = ConfigDict(frozen=True)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    array_capacity_kwp: float
    panel_count: int
    panel_rating_kwp: float
    orientation: str
    azimuth_degrees: float
    roof_pitch_degrees: float
    significant_shading: str
    public_region: str
    pvgis_loss_percent: float | None
    raw_weather_dir: Path = DEFAULT_RAW_WEATHER_DIR
    processed_weather_dir: Path = DEFAULT_PROCESSED_WEATHER_DIR
    database_path: Path = DEFAULT_WEATHER_DB_PATH


def load_weather_config(path: Path = Path(".env")) -> WeatherConfig:
    wattson = load_wattson_config()
    values: dict[str, str] = {}
    if path.exists():
        values.update(read_dotenv(path))
    values.update({key: value for key, value in os.environ.items() if value})
    missing = [key for key in WEATHER_ENV_KEYS if not values.get(key)]
    if missing:
        raise ValueError(f"Missing required environment values: {', '.join(missing)}")
    return WeatherConfig(
        latitude=float(values["WATTSON_LATITUDE"]),
        longitude=float(values["WATTSON_LONGITUDE"]),
        array_capacity_kwp=wattson.solar.installed_capacity_kwp,
        panel_count=wattson.solar.panel_count,
        panel_rating_kwp=wattson.solar.panel_rating_kw,
        orientation=wattson.solar.orientation.description,
        azimuth_degrees=wattson.solar.orientation.azimuth_deg,
        roof_pitch_degrees=wattson.solar.roof_pitch_deg,
        significant_shading="yes" if wattson.solar.significant_shading else "none",
        public_region=wattson.site.public_region,
        pvgis_loss_percent=wattson.weather.pvgis.system_loss_percent,
    )


def redacted_location(config: WeatherConfig) -> dict[str, str]:
    """Return the only location shape suitable for logs or public reports."""
    return {"region": config.public_region}
