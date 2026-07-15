"""Weather collection, normalisation and evaluation dataset builders."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import pandas as pd

from metrics.energy import DEFAULT_PARQUET_PATH, GENERATION_COLUMN
from utils.redaction import text_hash
from weather.client import OpenMeteoClient, PVGISClient, write_sanitized_raw
from weather.config import (
    DEFAULT_PROCESSED_WEATHER_DIR,
    WeatherConfig,
    load_weather_config,
)
from weather.models import (
    PVGISBaseline,
    SolarRadiationObservation,
    WeatherForecastInterval,
    WeatherForecastRun,
    WeatherObservationInterval,
)
from weather.store import WeatherStore

LONDON = ZoneInfo("Europe/London")
UTC_ZONE = ZoneInfo("UTC")
SOLA_X_HISTORY_START = date(2023, 1, 24)


def collect_forecast(
    config: WeatherConfig | None = None,
    store: WeatherStore | None = None,
    client: OpenMeteoClient | None = None,
    now: datetime | None = None,
) -> tuple[WeatherForecastRun, list[WeatherForecastInterval]]:
    cfg = config or load_weather_config()
    weather_store = store or WeatherStore(cfg.database_path)
    should_close = store is None
    retrieved_at = normalize_utc(now or datetime.now(UTC))
    open_meteo = client or OpenMeteoClient()
    url = open_meteo.forecast_url(cfg)
    payload = open_meteo.get_json(url)
    raw_hash = text_hash(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    write_sanitized_raw(
        cfg.raw_weather_dir
        / "open_meteo"
        / f"forecast_{retrieved_at:%Y%m%dT%H%M%SZ}_{raw_hash[:12]}.json",
        payload,
    )
    run, intervals = normalize_forecast_payload(payload, url, retrieved_at, raw_hash)
    weather_store.insert_forecast_run(run, intervals)
    write_processed_weather(weather_store, cfg.processed_weather_dir)
    if should_close:
        weather_store.close()
    return run, intervals


def collect_observations(
    start: date = SOLA_X_HISTORY_START,
    end: date | None = None,
    config: WeatherConfig | None = None,
    store: WeatherStore | None = None,
    client: OpenMeteoClient | None = None,
    now: datetime | None = None,
) -> list[WeatherObservationInterval]:
    cfg = config or load_weather_config()
    weather_store = store or WeatherStore(cfg.database_path)
    should_close = store is None
    collected_at = normalize_utc(now or datetime.now(UTC))
    stop = end or collected_at.date()
    open_meteo = client or OpenMeteoClient()
    url = open_meteo.archive_url(cfg, start, stop)
    payload = open_meteo.get_json(url)
    raw_hash = text_hash(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    write_sanitized_raw(
        cfg.raw_weather_dir / "open_meteo" / f"archive_{start}_{stop}_{raw_hash[:12]}.json",
        payload,
    )
    rows = normalize_observation_payload(payload, url, raw_hash, collected_at, "reanalysis")
    weather_store.insert_observations(rows)
    write_processed_weather(weather_store, cfg.processed_weather_dir)
    if should_close:
        weather_store.close()
    return rows


def collect_historical_forecast_backfill(
    start: date,
    end: date,
    config: WeatherConfig | None = None,
    store: WeatherStore | None = None,
    client: OpenMeteoClient | None = None,
    now: datetime | None = None,
) -> tuple[WeatherForecastRun, list[WeatherForecastInterval]]:
    cfg = config or load_weather_config()
    weather_store = store or WeatherStore(cfg.database_path)
    should_close = store is None
    retrieved_at = normalize_utc(now or datetime.now(UTC))
    open_meteo = client or OpenMeteoClient()
    url = open_meteo.historical_forecast_url(cfg, start, end)
    payload = open_meteo.get_json(url)
    raw_hash = text_hash(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    write_sanitized_raw(
        cfg.raw_weather_dir
        / "open_meteo"
        / f"historical_forecast_{start}_{end}_{raw_hash[:12]}.json",
        payload,
    )
    run, intervals = normalize_forecast_payload(payload, url, retrieved_at, raw_hash)
    run = run.model_copy(update={"source": "open_meteo_historical_forecast"})
    intervals = [
        interval.model_copy(update={"source": "open_meteo_historical_forecast"})
        for interval in intervals
    ]
    weather_store.insert_forecast_run(run, intervals)
    write_processed_weather(weather_store, cfg.processed_weather_dir)
    if should_close:
        weather_store.close()
    return run, intervals


def collect_satellite_radiation(
    start: date,
    end: date,
    config: WeatherConfig | None = None,
    store: WeatherStore | None = None,
    client: OpenMeteoClient | None = None,
    now: datetime | None = None,
) -> list[SolarRadiationObservation]:
    cfg = config or load_weather_config()
    weather_store = store or WeatherStore(cfg.database_path)
    should_close = store is None
    collected_at = normalize_utc(now or datetime.now(UTC))
    open_meteo = client or OpenMeteoClient()
    url = open_meteo.satellite_radiation_url(cfg, start, end)
    payload = open_meteo.get_json(url)
    raw_hash = text_hash(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    write_sanitized_raw(
        cfg.raw_weather_dir
        / "open_meteo"
        / f"satellite_radiation_{start}_{end}_{raw_hash[:12]}.json",
        payload,
    )
    rows = normalize_satellite_radiation_payload(payload, url, raw_hash, collected_at)
    weather_store.insert_solar_radiation(rows)
    write_processed_weather(weather_store, cfg.processed_weather_dir)
    if should_close:
        weather_store.close()
    return rows


def build_pvgis_baseline(
    config: WeatherConfig | None = None,
    store: WeatherStore | None = None,
    client: PVGISClient | None = None,
    now: datetime | None = None,
) -> PVGISBaseline:
    cfg = config or load_weather_config()
    weather_store = store or WeatherStore(cfg.database_path)
    should_close = store is None
    collected_at = normalize_utc(now or datetime.now(UTC))
    pvgis = client or PVGISClient()
    url = pvgis.pvcalc_url(cfg)
    payload = pvgis.get_json(url)
    raw_hash = text_hash(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    write_sanitized_raw(
        cfg.raw_weather_dir
        / "pvgis"
        / f"pvcalc_{collected_at:%Y%m%dT%H%M%SZ}_{raw_hash[:12]}.json",
        payload,
    )
    baseline = normalize_pvgis_payload(payload, url, raw_hash, collected_at, cfg)
    weather_store.insert_pvgis_baseline(baseline)
    write_processed_weather(weather_store, cfg.processed_weather_dir)
    if should_close:
        weather_store.close()
    return baseline


def build_evaluation_dataset(
    forecast_frame: pd.DataFrame,
    solax_parquet_path: Path = DEFAULT_PARQUET_PATH,
    observation_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    solax = pd.read_parquet(solax_parquet_path).copy()
    solax["interval_start"] = pd.to_datetime(solax["interval_start"])
    solax["hour_utc"] = solax["interval_start"].dt.tz_localize(None).dt.floor("h")
    hourly_actual = (
        solax.groupby("hour_utc", as_index=False)[GENERATION_COLUMN]
        .sum()
        .rename(columns={GENERATION_COLUMN: "actual_generation_kwh"})
    )
    forecasts = forecast_frame.copy()
    forecasts["target_time_utc"] = pd.to_datetime(forecasts["target_time_utc"]).dt.tz_localize(None)
    forecasts = forecasts.rename(columns={"target_time_utc": "hour_utc"})
    joined = forecasts.merge(hourly_actual, on="hour_utc", how="left")
    if observation_frame is not None and not observation_frame.empty:
        obs = observation_frame.copy()
        obs["interval_start_utc"] = pd.to_datetime(obs["interval_start_utc"]).dt.tz_localize(None)
        obs = obs.rename(
            columns={
                "interval_start_utc": "hour_utc",
                "global_horizontal_irradiance_w_m2": "observed_global_horizontal_irradiance_w_m2",
            }
        )
        joined = joined.merge(
            obs[["hour_utc", "observed_global_horizontal_irradiance_w_m2"]],
            on="hour_utc",
            how="left",
        )
    joined["data_quality_status"] = (
        joined["actual_generation_kwh"]
        .isna()
        .map({True: "missing_actual_generation", False: "ready_for_validation"})
    )
    return joined


def normalize_forecast_payload(
    payload: dict[str, Any], endpoint: str, retrieved_at: datetime, raw_hash: str
) -> tuple[WeatherForecastRun, list[WeatherForecastInterval]]:
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    model = str(payload.get("model") or payload.get("models") or "best_match")
    issue_time = forecast_issue_time(payload, retrieved_at)
    run_id = text_hash(f"open_meteo|{model}|{issue_time.isoformat()}|{raw_hash}")[:32]
    daily = daily_sun_map(payload.get("daily", {}))
    intervals: list[WeatherForecastInterval] = []
    for index, raw_time in enumerate(times):
        target_local = parse_local_time(raw_time)
        target_utc = target_local.astimezone(UTC_ZONE)
        flags: list[str] = []
        lead_hours = round((target_utc - issue_time).total_seconds() / 3600, 3)
        if lead_hours < 0:
            flags.append("target_before_issue_time")
        if get_hourly(hourly, "shortwave_radiation", index) is None:
            flags.append("missing_global_horizontal_irradiance")
        if target_local.hour < 5 or target_local.hour > 22:
            flags.append("night_or_low_sun")
        sunrise, sunset = daily.get(target_local.date().isoformat(), (None, None))
        intervals.append(
            WeatherForecastInterval(
                run_id=run_id,
                target_time_utc=target_utc,
                target_time_local=target_local.isoformat(),
                lead_hours=lead_hours,
                source="open_meteo",
                model=model,
                global_horizontal_irradiance_w_m2=get_hourly(hourly, "shortwave_radiation", index),
                direct_normal_irradiance_w_m2=get_hourly(hourly, "direct_normal_irradiance", index),
                diffuse_horizontal_irradiance_w_m2=get_hourly(hourly, "diffuse_radiation", index),
                tilted_plane_irradiance_w_m2=get_hourly(hourly, "global_tilted_irradiance", index),
                cloud_cover_percent=get_hourly(hourly, "cloud_cover", index),
                cloud_cover_low_percent=get_hourly(hourly, "cloud_cover_low", index),
                cloud_cover_mid_percent=get_hourly(hourly, "cloud_cover_mid", index),
                cloud_cover_high_percent=get_hourly(hourly, "cloud_cover_high", index),
                temperature_c=get_hourly(hourly, "temperature_2m", index),
                relative_humidity_percent=get_hourly(hourly, "relative_humidity_2m", index),
                precipitation_mm=get_hourly(hourly, "precipitation", index),
                precipitation_probability_percent=get_hourly(
                    hourly, "precipitation_probability", index
                ),
                wind_speed_kmh=get_hourly(hourly, "wind_speed_10m", index),
                weather_code=get_hourly_int(hourly, "weather_code", index),
                sunrise_local=sunrise,
                sunset_local=sunset,
                daylight=bool(get_hourly_int(hourly, "is_day", index))
                if "is_day" in hourly
                else None,
                quality_flags=tuple(flags),
            )
        )
    run = WeatherForecastRun(
        run_id=run_id,
        source="open_meteo",
        model=model,
        issue_time_utc=issue_time,
        retrieved_at_utc=retrieved_at,
        raw_response_hash=raw_hash,
        source_endpoint=endpoint_without_coordinates(endpoint),
    )
    return run, intervals


def normalize_observation_payload(
    payload: dict[str, Any],
    endpoint: str,
    raw_hash: str,
    ingestion_timestamp: datetime,
    dataset_type: str,
) -> list[WeatherObservationInterval]:
    hourly = payload.get("hourly", {})
    rows: list[WeatherObservationInterval] = []
    for index, raw_time in enumerate(hourly.get("time", [])):
        local_time = parse_local_time(raw_time)
        rows.append(
            WeatherObservationInterval(
                source="open_meteo",
                dataset_type=dataset_type,
                interval_start_utc=local_time.astimezone(UTC_ZONE),
                interval_start_local=local_time.isoformat(),
                global_horizontal_irradiance_w_m2=get_hourly(hourly, "shortwave_radiation", index),
                direct_normal_irradiance_w_m2=get_hourly(hourly, "direct_normal_irradiance", index),
                diffuse_horizontal_irradiance_w_m2=get_hourly(hourly, "diffuse_radiation", index),
                tilted_plane_irradiance_w_m2=get_hourly(hourly, "global_tilted_irradiance", index),
                cloud_cover_percent=get_hourly(hourly, "cloud_cover", index),
                temperature_c=get_hourly(hourly, "temperature_2m", index),
                precipitation_mm=get_hourly(hourly, "precipitation", index),
                source_endpoint=endpoint_without_coordinates(endpoint),
                raw_response_hash=raw_hash,
                ingestion_timestamp_utc=ingestion_timestamp,
                quality_flags=(),
            )
        )
    return rows


def normalize_satellite_radiation_payload(
    payload: dict[str, Any],
    endpoint: str,
    raw_hash: str,
    ingestion_timestamp: datetime,
) -> list[SolarRadiationObservation]:
    hourly = payload.get("hourly", {})
    rows: list[SolarRadiationObservation] = []
    for index, raw_time in enumerate(hourly.get("time", [])):
        local_time = parse_local_time(raw_time)
        rows.append(
            SolarRadiationObservation(
                source="open_meteo_satellite",
                interval_start_utc=local_time.astimezone(UTC_ZONE),
                interval_start_local=local_time.isoformat(),
                global_horizontal_irradiance_w_m2=get_hourly(hourly, "shortwave_radiation", index),
                direct_normal_irradiance_w_m2=get_hourly(hourly, "direct_normal_irradiance", index),
                diffuse_horizontal_irradiance_w_m2=get_hourly(hourly, "diffuse_radiation", index),
                source_endpoint=endpoint_without_coordinates(endpoint),
                raw_response_hash=raw_hash,
                ingestion_timestamp_utc=ingestion_timestamp,
            )
        )
    return rows


def normalize_pvgis_payload(
    payload: dict[str, Any],
    endpoint: str,
    raw_hash: str,
    ingestion_timestamp: datetime,
    config: WeatherConfig,
) -> PVGISBaseline:
    outputs = payload.get("outputs", {})
    monthly = outputs.get("monthly", {}).get("fixed", [])
    monthly_generation = {
        f"{int(row.get('month', index + 1)):02d}": round(float(row.get("E_m", 0.0)), 3)
        for index, row in enumerate(monthly)
    }
    annual = float(outputs.get("totals", {}).get("fixed", {}).get("E_y", 0.0))
    meta = payload.get("meta", {})
    inputs = payload.get("inputs", {})
    return PVGISBaseline(
        baseline_id=text_hash(f"pvgis|{raw_hash}|{config.array_capacity_kwp}")[:32],
        dataset=str(meta.get("radiation_database") or inputs.get("raddatabase") or "PVGIS"),
        version=str(meta.get("version") or "PVGIS 5.3"),
        public_region=config.public_region,
        installed_capacity_kwp=config.array_capacity_kwp,
        tilt_degrees=config.roof_pitch_degrees,
        azimuth_degrees=config.azimuth_degrees,
        system_loss_percent=config.pvgis_loss_percent,
        fixed_mounting=True,
        monthly_expected_generation_kwh=monthly_generation,
        annual_expected_generation_kwh=round(annual, 3),
        assumptions_json=json.dumps(
            {
                "mounting": "fixed building-mounted",
                "orientation": config.orientation,
                "significant_shading": config.significant_shading,
                "benchmark_only": True,
            },
            sort_keys=True,
        ),
        raw_response_hash=raw_hash,
        source_endpoint=endpoint_without_coordinates(endpoint),
        ingestion_timestamp_utc=ingestion_timestamp,
    )


def write_processed_weather(
    store: WeatherStore, output_dir: Path = DEFAULT_PROCESSED_WEATHER_DIR
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for table in [
        "weather_forecast_run",
        "weather_forecast_interval",
        "weather_observation_interval",
        "solar_radiation_observation",
        "pvgis_baseline",
        "forecast_quality_event",
    ]:
        frame = store.table_frame(table)
        frame.to_parquet(output_dir / f"{table}.parquet", index=False)


def build_calendar_month_average_baseline(actual_hourly: pd.DataFrame) -> pd.DataFrame:
    frame = actual_hourly.copy()
    frame["hour_utc"] = pd.to_datetime(frame["hour_utc"])
    frame["calendar_month"] = frame["hour_utc"].dt.month
    return (
        frame.groupby("calendar_month", as_index=False)["actual_generation_kwh"]
        .mean()
        .rename(columns={"actual_generation_kwh": "calendar_month_average_generation_kwh"})
    )


def build_recent_day_persistence_baseline(actual_hourly: pd.DataFrame) -> pd.DataFrame:
    frame = actual_hourly.copy()
    frame["hour_utc"] = pd.to_datetime(frame["hour_utc"])
    previous = frame[["hour_utc", "actual_generation_kwh"]].copy()
    previous["hour_utc"] = previous["hour_utc"] + pd.Timedelta(days=1)
    return (
        frame[["hour_utc"]]
        .merge(previous, on="hour_utc", how="left")
        .rename(columns={"actual_generation_kwh": "recent_day_persistence_generation_kwh"})
    )


def irradiance_scaled_capacity_baseline(
    forecast_frame: pd.DataFrame, array_capacity_kwp: float
) -> pd.DataFrame:
    frame = forecast_frame.copy()
    ghi = pd.to_numeric(frame["global_horizontal_irradiance_w_m2"], errors="coerce").fillna(0)
    frame["irradiance_scaled_capacity_generation_kwh"] = (ghi / 1000.0) * array_capacity_kwp
    return frame


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC_ZONE)
    return value.astimezone(UTC_ZONE)


def parse_local_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LONDON)
    return parsed.astimezone(LONDON)


def forecast_issue_time(payload: dict[str, Any], fallback: datetime) -> datetime:
    for key in ["model_run", "model_run_time", "issue_time"]:
        if payload.get(key):
            return normalize_utc(datetime.fromisoformat(str(payload[key]).replace("Z", "+00:00")))
    return fallback.replace(minute=0, second=0, microsecond=0)


def daily_sun_map(daily: dict[str, Any]) -> dict[str, tuple[str | None, str | None]]:
    times = daily.get("time", [])
    sunrise = daily.get("sunrise", [])
    sunset = daily.get("sunset", [])
    return {
        str(day): (
            sunrise[index] if index < len(sunrise) else None,
            sunset[index] if index < len(sunset) else None,
        )
        for index, day in enumerate(times)
    }


def get_hourly(hourly: dict[str, Any], key: str, index: int) -> float | None:
    values = hourly.get(key)
    if not isinstance(values, list) or index >= len(values) or values[index] is None:
        return None
    return float(values[index])


def get_hourly_int(hourly: dict[str, Any], key: str, index: int) -> int | None:
    value = get_hourly(hourly, key, index)
    return None if value is None else int(value)


def endpoint_without_coordinates(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    query = [
        (key, "[redacted]" if key.lower() in {"latitude", "longitude", "lat", "lon"} else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def due(last_success: datetime | None, now: datetime, interval: timedelta) -> bool:
    return last_success is None or now - last_success >= interval
