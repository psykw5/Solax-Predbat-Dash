"""Open-Meteo and PVGIS HTTP clients."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from utils.redaction import text_hash
from weather.config import WeatherConfig

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
OPEN_METEO_SATELLITE_RADIATION_URL = "https://satellite-api.open-meteo.com/v1/archive"
PVGIS_PVCALC_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"

HOURLY_FORECAST_VARIABLES = [
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "global_tilted_irradiance",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "precipitation_probability",
    "wind_speed_10m",
    "weather_code",
    "is_day",
]

DAILY_FORECAST_VARIABLES = ["sunrise", "sunset"]


class OpenMeteoClient:
    def get_json(self, url: str) -> dict[str, Any]:
        with urlopen(url, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def forecast_url(self, config: WeatherConfig, forecast_days: int = 7) -> str:
        query = {
            "latitude": config.latitude,
            "longitude": config.longitude,
            "hourly": ",".join(HOURLY_FORECAST_VARIABLES),
            "daily": ",".join(DAILY_FORECAST_VARIABLES),
            "timezone": "Europe/London",
            "timeformat": "iso8601",
            "forecast_days": forecast_days,
            "tilt": config.roof_pitch_degrees,
            "azimuth": config.azimuth_degrees,
        }
        return f"{OPEN_METEO_FORECAST_URL}?{urlencode(query)}"

    def archive_url(self, config: WeatherConfig, start: date, end: date) -> str:
        variables = [
            "shortwave_radiation",
            "direct_normal_irradiance",
            "diffuse_radiation",
            "cloud_cover",
            "temperature_2m",
            "precipitation",
        ]
        query = {
            "latitude": config.latitude,
            "longitude": config.longitude,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": ",".join(variables),
            "timezone": "Europe/London",
            "timeformat": "iso8601",
            "tilt": config.roof_pitch_degrees,
            "azimuth": config.azimuth_degrees,
        }
        return f"{OPEN_METEO_ARCHIVE_URL}?{urlencode(query)}"

    def historical_forecast_url(self, config: WeatherConfig, start: date, end: date) -> str:
        query = {
            "latitude": config.latitude,
            "longitude": config.longitude,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": ",".join(HOURLY_FORECAST_VARIABLES),
            "daily": ",".join(DAILY_FORECAST_VARIABLES),
            "timezone": "Europe/London",
            "timeformat": "iso8601",
            "tilt": config.roof_pitch_degrees,
            "azimuth": config.azimuth_degrees,
        }
        return f"{OPEN_METEO_HISTORICAL_FORECAST_URL}?{urlencode(query)}"

    def satellite_radiation_url(self, config: WeatherConfig, start: date, end: date) -> str:
        variables = ["shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation"]
        query = {
            "latitude": config.latitude,
            "longitude": config.longitude,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": ",".join(variables),
            "timezone": "Europe/London",
            "timeformat": "iso8601",
        }
        return f"{OPEN_METEO_SATELLITE_RADIATION_URL}?{urlencode(query)}"


class PVGISClient:
    def get_json(self, url: str) -> dict[str, Any]:
        with urlopen(url, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def pvcalc_url(self, config: WeatherConfig) -> str:
        query = {
            "lat": config.latitude,
            "lon": config.longitude,
            "peakpower": config.array_capacity_kwp,
            "fixed": 1,
            "angle": config.roof_pitch_degrees,
            "aspect": compass_azimuth_to_pvgis_aspect(config.azimuth_degrees),
            "mountingplace": "building",
            "outputformat": "json",
        }
        if config.pvgis_loss_percent is not None:
            query["loss"] = config.pvgis_loss_percent
        return f"{PVGIS_PVCALC_URL}?{urlencode(query)}"


def write_sanitized_raw(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sanitize_weather_payload(payload), indent=2, sort_keys=True), "utf-8"
    )
    return text_hash(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def sanitize_weather_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"latitude", "longitude", "lat", "lon"}:
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_weather_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_weather_payload(item) for item in value]
    return value


def compass_azimuth_to_pvgis_aspect(azimuth_degrees: float) -> float:
    """Convert compass azimuth to PVGIS aspect.

    Wattson stores compass azimuth with north=0, east=90, south=180 and west=270.
    PVGIS aspect uses south=0, east=-90 and west=90.
    """
    return ((azimuth_degrees - 180 + 180) % 360) - 180
