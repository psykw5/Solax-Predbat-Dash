"""Typed weather and forecast records."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WeatherForecastRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    source: str
    model: str
    issue_time_utc: datetime
    retrieved_at_utc: datetime
    raw_response_hash: str
    source_endpoint: str
    status: str = "valid"


class WeatherForecastInterval(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    target_time_utc: datetime
    target_time_local: str
    lead_hours: float
    source: str
    model: str
    global_horizontal_irradiance_w_m2: float | None = None
    direct_normal_irradiance_w_m2: float | None = None
    diffuse_horizontal_irradiance_w_m2: float | None = None
    tilted_plane_irradiance_w_m2: float | None = None
    cloud_cover_percent: float | None = None
    cloud_cover_low_percent: float | None = None
    cloud_cover_mid_percent: float | None = None
    cloud_cover_high_percent: float | None = None
    temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    precipitation_mm: float | None = None
    precipitation_probability_percent: float | None = None
    wind_speed_kmh: float | None = None
    weather_code: int | None = None
    sunrise_local: str | None = None
    sunset_local: str | None = None
    daylight: bool | None = None
    quality_flags: tuple[str, ...] = ()


class WeatherObservationInterval(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    dataset_type: str
    interval_start_utc: datetime
    interval_start_local: str
    global_horizontal_irradiance_w_m2: float | None = None
    direct_normal_irradiance_w_m2: float | None = None
    diffuse_horizontal_irradiance_w_m2: float | None = None
    tilted_plane_irradiance_w_m2: float | None = None
    cloud_cover_percent: float | None = None
    temperature_c: float | None = None
    precipitation_mm: float | None = None
    source_endpoint: str = ""
    raw_response_hash: str = ""
    ingestion_timestamp_utc: datetime
    quality_flags: tuple[str, ...] = ()


class SolarRadiationObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    interval_start_utc: datetime
    interval_start_local: str
    global_horizontal_irradiance_w_m2: float | None = None
    direct_normal_irradiance_w_m2: float | None = None
    diffuse_horizontal_irradiance_w_m2: float | None = None
    source_endpoint: str = ""
    raw_response_hash: str = ""
    ingestion_timestamp_utc: datetime


class PVGISBaseline(BaseModel):
    model_config = ConfigDict(frozen=True)

    baseline_id: str
    source: str = "pvgis"
    dataset: str
    version: str
    public_region: str
    installed_capacity_kwp: float
    tilt_degrees: float
    azimuth_degrees: float
    system_loss_percent: float | None
    fixed_mounting: bool
    monthly_expected_generation_kwh: dict[str, float]
    annual_expected_generation_kwh: float
    assumptions_json: str
    raw_response_hash: str
    source_endpoint: str
    ingestion_timestamp_utc: datetime


class ForecastQualityEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: str
    severity: str
    message: str
    observed_at_utc: datetime


class ForecastEvaluationMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    model: str
    lead_hours: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    mae_kwh: float
    rmse_kwh: float
    mean_bias_error_kwh: float
    mean_percentage_error: float | None
