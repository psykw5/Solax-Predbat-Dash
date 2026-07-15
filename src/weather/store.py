"""SQLite persistence for weather and solar forecast data."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from weather.config import DEFAULT_WEATHER_DB_PATH
from weather.models import (
    ForecastQualityEvent,
    PVGISBaseline,
    SolarRadiationObservation,
    WeatherForecastInterval,
    WeatherForecastRun,
    WeatherObservationInterval,
)


class WeatherStore:
    def __init__(self, path: Path = DEFAULT_WEATHER_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
        self.connection.executescript(
            """
            create table if not exists weather_forecast_run (
                run_id text primary key,
                source text not null,
                model text not null,
                issue_time_utc text not null,
                retrieved_at_utc text not null,
                raw_response_hash text not null,
                source_endpoint text not null,
                status text not null
            );
            create table if not exists weather_forecast_interval (
                run_id text not null,
                target_time_utc text not null,
                target_time_local text not null,
                lead_hours real not null,
                source text not null,
                model text not null,
                global_horizontal_irradiance_w_m2 real,
                direct_normal_irradiance_w_m2 real,
                diffuse_horizontal_irradiance_w_m2 real,
                tilted_plane_irradiance_w_m2 real,
                cloud_cover_percent real,
                cloud_cover_low_percent real,
                cloud_cover_mid_percent real,
                cloud_cover_high_percent real,
                temperature_c real,
                relative_humidity_percent real,
                precipitation_mm real,
                precipitation_probability_percent real,
                wind_speed_kmh real,
                weather_code integer,
                sunrise_local text,
                sunset_local text,
                daylight integer,
                quality_flags_json text not null,
                primary key (run_id, target_time_utc)
            );
            create table if not exists weather_observation_interval (
                source text not null,
                dataset_type text not null,
                interval_start_utc text not null,
                interval_start_local text not null,
                global_horizontal_irradiance_w_m2 real,
                direct_normal_irradiance_w_m2 real,
                diffuse_horizontal_irradiance_w_m2 real,
                tilted_plane_irradiance_w_m2 real,
                cloud_cover_percent real,
                temperature_c real,
                precipitation_mm real,
                source_endpoint text not null,
                raw_response_hash text not null,
                ingestion_timestamp_utc text not null,
                quality_flags_json text not null,
                primary key (source, dataset_type, interval_start_utc)
            );
            create table if not exists solar_radiation_observation (
                source text not null,
                interval_start_utc text not null,
                interval_start_local text not null,
                global_horizontal_irradiance_w_m2 real,
                direct_normal_irradiance_w_m2 real,
                diffuse_horizontal_irradiance_w_m2 real,
                source_endpoint text not null,
                raw_response_hash text not null,
                ingestion_timestamp_utc text not null,
                primary key (source, interval_start_utc)
            );
            create table if not exists pvgis_baseline (
                baseline_id text primary key,
                source text not null,
                dataset text not null,
                version text not null,
                public_region text not null,
                installed_capacity_kwp real not null,
                tilt_degrees real not null,
                azimuth_degrees real not null,
                system_loss_percent real,
                fixed_mounting integer not null,
                monthly_expected_generation_json text not null,
                annual_expected_generation_kwh real not null,
                assumptions_json text not null,
                raw_response_hash text not null,
                source_endpoint text not null,
                ingestion_timestamp_utc text not null
            );
            create table if not exists forecast_quality_event (
                event_type text not null,
                severity text not null,
                message text not null,
                observed_at_utc text not null,
                primary key (event_type, observed_at_utc, message)
            );
            """
        )
        self.connection.commit()

    def insert_forecast_run(
        self, run: WeatherForecastRun, intervals: list[WeatherForecastInterval]
    ) -> tuple[bool, int]:
        cursor = self.connection.execute(
            "insert or ignore into weather_forecast_run values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.run_id,
                run.source,
                run.model,
                run.issue_time_utc.isoformat(),
                run.retrieved_at_utc.isoformat(),
                run.raw_response_hash,
                run.source_endpoint,
                run.status,
            ),
        )
        self.connection.executemany(
            """
            insert or ignore into weather_forecast_interval values (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [forecast_interval_values(interval) for interval in intervals],
        )
        inserted_intervals = self.connection.execute("select changes()").fetchone()[0]
        self.connection.commit()
        return cursor.rowcount > 0, int(inserted_intervals)

    def insert_observations(self, rows: list[WeatherObservationInterval]) -> int:
        self.connection.executemany(
            """
            insert or ignore into weather_observation_interval values (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [observation_values(row) for row in rows],
        )
        inserted = self.connection.execute("select changes()").fetchone()[0]
        self.connection.commit()
        return int(inserted)

    def insert_solar_radiation(self, rows: list[SolarRadiationObservation]) -> int:
        self.connection.executemany(
            "insert or ignore into solar_radiation_observation values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.source,
                    row.interval_start_utc.isoformat(),
                    row.interval_start_local,
                    row.global_horizontal_irradiance_w_m2,
                    row.direct_normal_irradiance_w_m2,
                    row.diffuse_horizontal_irradiance_w_m2,
                    row.source_endpoint,
                    row.raw_response_hash,
                    row.ingestion_timestamp_utc.isoformat(),
                )
                for row in rows
            ],
        )
        inserted = self.connection.execute("select changes()").fetchone()[0]
        self.connection.commit()
        return int(inserted)

    def insert_pvgis_baseline(self, baseline: PVGISBaseline) -> bool:
        cursor = self.connection.execute(
            """
            insert or replace into pvgis_baseline values (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                baseline.baseline_id,
                baseline.source,
                baseline.dataset,
                baseline.version,
                baseline.public_region,
                baseline.installed_capacity_kwp,
                baseline.tilt_degrees,
                baseline.azimuth_degrees,
                baseline.system_loss_percent,
                1 if baseline.fixed_mounting else 0,
                json.dumps(baseline.monthly_expected_generation_kwh, sort_keys=True),
                baseline.annual_expected_generation_kwh,
                baseline.assumptions_json,
                baseline.raw_response_hash,
                baseline.source_endpoint,
                baseline.ingestion_timestamp_utc.isoformat(),
            ),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def insert_quality_event(self, event: ForecastQualityEvent) -> None:
        self.connection.execute(
            "insert or ignore into forecast_quality_event values (?, ?, ?, ?)",
            (event.event_type, event.severity, event.message, event.observed_at_utc.isoformat()),
        )
        self.connection.commit()

    def table_frame(self, table: str) -> pd.DataFrame:
        return pd.read_sql_query(f"select * from {table}", self.connection)


def forecast_interval_values(row: WeatherForecastInterval) -> tuple[object, ...]:
    return (
        row.run_id,
        row.target_time_utc.isoformat(),
        row.target_time_local,
        row.lead_hours,
        row.source,
        row.model,
        row.global_horizontal_irradiance_w_m2,
        row.direct_normal_irradiance_w_m2,
        row.diffuse_horizontal_irradiance_w_m2,
        row.tilted_plane_irradiance_w_m2,
        row.cloud_cover_percent,
        row.cloud_cover_low_percent,
        row.cloud_cover_mid_percent,
        row.cloud_cover_high_percent,
        row.temperature_c,
        row.relative_humidity_percent,
        row.precipitation_mm,
        row.precipitation_probability_percent,
        row.wind_speed_kmh,
        row.weather_code,
        row.sunrise_local,
        row.sunset_local,
        None if row.daylight is None else int(row.daylight),
        json.dumps(row.quality_flags),
    )


def observation_values(row: WeatherObservationInterval) -> tuple[object, ...]:
    return (
        row.source,
        row.dataset_type,
        row.interval_start_utc.isoformat(),
        row.interval_start_local,
        row.global_horizontal_irradiance_w_m2,
        row.direct_normal_irradiance_w_m2,
        row.diffuse_horizontal_irradiance_w_m2,
        row.tilted_plane_irradiance_w_m2,
        row.cloud_cover_percent,
        row.temperature_c,
        row.precipitation_mm,
        row.source_endpoint,
        row.raw_response_hash,
        row.ingestion_timestamp_utc.isoformat(),
        json.dumps(row.quality_flags),
    )
