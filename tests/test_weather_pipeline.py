from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from weather.client import (
    OpenMeteoClient,
    PVGISClient,
    compass_azimuth_to_pvgis_aspect,
    sanitize_weather_payload,
)
from weather.config import WeatherConfig, load_weather_config, redacted_location
from weather.evaluation import evaluate_forecasts_by_lead_time, forecast_revision_magnitude
from weather.pipeline import (
    build_evaluation_dataset,
    build_pvgis_baseline,
    collect_forecast,
    collect_historical_forecast_backfill,
    collect_observations,
    collect_satellite_radiation,
    endpoint_without_coordinates,
    irradiance_scaled_capacity_baseline,
    normalize_forecast_payload,
)
from weather.store import WeatherStore


class WeatherPipelineTests(unittest.TestCase):
    def test_open_meteo_client_requires_no_credentials(self) -> None:
        client = OpenMeteoClient()
        url = client.forecast_url(test_config())

        self.assertIn("api.open-meteo.com", url)
        self.assertNotIn("apikey", url.lower())

    def test_private_coordinates_load_and_redact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("WATTSON_LATITUDE=52.1\nWATTSON_LONGITUDE=-1.2\n", encoding="utf-8")

            config = load_weather_config(env)

            self.assertEqual(config.latitude, 52.1)
            self.assertEqual(redacted_location(config), {"region": "Midlands, UK"})
            self.assertNotIn(
                "52.1", json.dumps(sanitize_weather_payload(config.model_dump()), default=str)
            )

    def test_forecast_vintage_preservation_and_duplicate_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = test_config(Path(tmp))
            store = WeatherStore(config.database_path)
            client = FakeOpenMeteoClient(forecast_payload())

            run, intervals = collect_forecast(
                config=config,
                store=store,
                client=client,
                now=datetime(2026, 7, 14, 6, tzinfo=UTC),
            )
            duplicate_run, duplicate_intervals = collect_forecast(
                config=config,
                store=store,
                client=client,
                now=datetime(2026, 7, 14, 6, tzinfo=UTC),
            )

            self.assertEqual(run.run_id, duplicate_run.run_id)
            self.assertEqual(len(intervals), len(duplicate_intervals))
            self.assertEqual(
                store.connection.execute("select count(*) from weather_forecast_run").fetchone()[0],
                1,
            )
            self.assertEqual(
                store.connection.execute(
                    "select count(*) from weather_forecast_interval"
                ).fetchone()[0],
                3,
            )
            store.close()

    def test_utc_bst_handling_and_night_intervals(self) -> None:
        run, intervals = normalize_forecast_payload(
            forecast_payload(times=["2026-10-25T00:00", "2026-10-25T02:00"]),
            "https://example.invalid?latitude=52&longitude=-1",
            datetime(2026, 10, 24, 18, tzinfo=UTC),
            "hash",
        )

        self.assertEqual(run.source, "open_meteo")
        self.assertTrue(all(interval.target_time_utc.tzinfo is not None for interval in intervals))
        self.assertIn("night_or_low_sun", intervals[0].quality_flags)

    def test_target_before_issue_time_is_flagged(self) -> None:
        _, intervals = normalize_forecast_payload(
            forecast_payload(times=["2026-07-14T10:00"]),
            "https://example.invalid",
            datetime(2026, 7, 14, 12, tzinfo=UTC),
            "hash",
        )

        self.assertLess(intervals[0].lead_hours, 0)
        self.assertIn("target_before_issue_time", intervals[0].quality_flags)

    def test_missing_irradiance_fields_are_flagged(self) -> None:
        payload = forecast_payload()
        payload["hourly"].pop("shortwave_radiation")

        _, intervals = normalize_forecast_payload(
            payload, "https://example.invalid", datetime(2026, 7, 14, 6, tzinfo=UTC), "hash"
        )

        self.assertIn("missing_global_horizontal_irradiance", intervals[0].quality_flags)

    def test_historical_forecast_and_reanalysis_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = test_config(Path(tmp))
            store = WeatherStore(config.database_path)
            rows = collect_observations(
                start=date(2026, 7, 13),
                end=date(2026, 7, 13),
                config=config,
                store=store,
                client=FakeOpenMeteoClient(archive_payload()),
                now=datetime(2026, 7, 14, tzinfo=UTC),
            )

            self.assertEqual(rows[0].dataset_type, "reanalysis")
            self.assertEqual(
                store.connection.execute(
                    "select count(*) from weather_observation_interval where dataset_type='reanalysis'"
                ).fetchone()[0],
                2,
            )
            store.close()

    def test_historical_forecast_backfill_is_distinct_from_reanalysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = test_config(Path(tmp))
            store = WeatherStore(config.database_path)

            run, intervals = collect_historical_forecast_backfill(
                date(2026, 7, 13),
                date(2026, 7, 13),
                config=config,
                store=store,
                client=FakeOpenMeteoClient(forecast_payload()),
                now=datetime(2026, 7, 14, tzinfo=UTC),
            )

            self.assertEqual(run.source, "open_meteo_historical_forecast")
            self.assertEqual(intervals[0].source, "open_meteo_historical_forecast")
            self.assertEqual(
                store.connection.execute(
                    "select count(*) from weather_observation_interval"
                ).fetchone()[0],
                0,
            )
            store.close()

    def test_satellite_radiation_backfill_is_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = test_config(Path(tmp))
            store = WeatherStore(config.database_path)

            rows = collect_satellite_radiation(
                date(2026, 7, 13),
                date(2026, 7, 13),
                config=config,
                store=store,
                client=FakeOpenMeteoClient(archive_payload()),
                now=datetime(2026, 7, 14, tzinfo=UTC),
            )

            self.assertEqual(rows[0].source, "open_meteo_satellite")
            self.assertEqual(
                store.connection.execute(
                    "select count(*) from solar_radiation_observation"
                ).fetchone()[0],
                2,
            )
            store.close()

    def test_idempotent_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = test_config(Path(tmp))
            store = WeatherStore(config.database_path)
            client = FakeOpenMeteoClient(archive_payload())

            collect_observations(date(2026, 7, 13), date(2026, 7, 13), config, store, client)
            collect_observations(date(2026, 7, 13), date(2026, 7, 13), config, store, client)

            self.assertEqual(
                store.connection.execute(
                    "select count(*) from weather_observation_interval"
                ).fetchone()[0],
                2,
            )
            store.close()

    def test_pvgis_orientation_and_tilt_configuration(self) -> None:
        client = PVGISClient()
        url = client.pvcalc_url(test_config())

        self.assertIn("peakpower=6.4", url)
        self.assertIn("angle=38.0", url)
        self.assertIn("aspect=0.0", url)

    def test_compass_azimuth_converts_to_pvgis_aspect(self) -> None:
        self.assertEqual(compass_azimuth_to_pvgis_aspect(180), 0)
        self.assertEqual(compass_azimuth_to_pvgis_aspect(90), -90)
        self.assertEqual(compass_azimuth_to_pvgis_aspect(270), 90)
        self.assertEqual(compass_azimuth_to_pvgis_aspect(0), -180)

    def test_pvgis_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = test_config(Path(tmp))
            store = WeatherStore(config.database_path)

            baseline = build_pvgis_baseline(
                config=config,
                store=store,
                client=FakePVGISClient(pvgis_payload()),
                now=datetime(2026, 7, 14, tzinfo=UTC),
            )

            self.assertEqual(baseline.public_region, "Midlands, UK")
            self.assertEqual(baseline.annual_expected_generation_kwh, 5800.0)
            self.assertEqual(baseline.monthly_expected_generation_kwh["01"], 100.0)
            store.close()

    def test_hourly_solax_generation_join(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            solax = Path(tmp) / "solax.parquet"
            pd.DataFrame(
                [
                    {
                        "interval_start": pd.Timestamp("2026-07-14 10:00:00"),
                        "interval_end": pd.Timestamp("2026-07-14 10:05:00"),
                        "pv_yield_kwh": 0.2,
                    },
                    {
                        "interval_start": pd.Timestamp("2026-07-14 10:05:00"),
                        "interval_end": pd.Timestamp("2026-07-14 10:10:00"),
                        "pv_yield_kwh": 0.3,
                    },
                ]
            ).to_parquet(solax, index=False)
            forecast = pd.DataFrame(
                [
                    {
                        "target_time_utc": "2026-07-14T10:00:00+00:00",
                        "source": "open_meteo",
                        "model": "best_match",
                        "lead_hours": 4,
                        "global_horizontal_irradiance_w_m2": 500.0,
                    }
                ]
            )

            joined = build_evaluation_dataset(forecast, solax)

            self.assertEqual(joined.loc[0, "actual_generation_kwh"], 0.5)
            self.assertEqual(joined.loc[0, "data_quality_status"], "ready_for_validation")

    def test_partial_weather_source_failure_can_record_quality_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WeatherStore(Path(tmp) / "weather.sqlite")
            store.insert_quality_event(
                event(
                    "open_meteo_collection_failed",
                    "warning",
                    "TimeoutError",
                    datetime(2026, 7, 14, tzinfo=UTC),
                )
            )

            self.assertEqual(
                store.connection.execute("select severity from forecast_quality_event").fetchone()[
                    0
                ],
                "warning",
            )
            store.close()

    def test_forecast_evaluation_by_lead_time(self) -> None:
        frame = pd.DataFrame(
            [
                row(1, 5.0, 4.0),
                row(1, 3.0, 4.0),
                row(2, 2.0, 1.0),
            ]
        )

        metrics = evaluate_forecasts_by_lead_time(frame)

        lead_one = [metric for metric in metrics if metric.lead_hours == 1][0]
        self.assertEqual(lead_one.sample_count, 2)
        self.assertEqual(lead_one.mae_kwh, 1.0)
        self.assertEqual(lead_one.mean_bias_error_kwh, 0.0)

    def test_forecast_revision_magnitude(self) -> None:
        frame = pd.DataFrame(
            [
                revision("2026-07-14T06:00:00Z", 2.0),
                revision("2026-07-14T12:00:00Z", 3.5),
            ]
        )

        revisions = forecast_revision_magnitude(frame)

        self.assertEqual(revisions.iloc[0]["revision_magnitude_kwh"], 1.5)

    def test_no_exact_coordinates_enter_sanitized_outputs(self) -> None:
        text = json.dumps(sanitize_weather_payload({"latitude": 52.1234, "longitude": -1.2345}))

        self.assertNotIn("52.1234", text)
        self.assertNotIn("-1.2345", text)

    def test_endpoint_redaction(self) -> None:
        endpoint = endpoint_without_coordinates("https://x?latitude=52.1&longitude=-1.2&hourly=x")

        self.assertNotIn("52.1", endpoint)
        self.assertNotIn("-1.2", endpoint)

    def test_irradiance_scaled_capacity_baseline(self) -> None:
        frame = irradiance_scaled_capacity_baseline(
            pd.DataFrame([{"global_horizontal_irradiance_w_m2": 500.0}]), 6.4
        )

        self.assertEqual(frame.loc[0, "irradiance_scaled_capacity_generation_kwh"], 3.2)


def test_config(root: Path | None = None) -> WeatherConfig:
    base = root or Path("data")
    return WeatherConfig(
        latitude=52.1,
        longitude=-1.2,
        array_capacity_kwp=6.4,
        panel_count=16,
        panel_rating_kwp=0.4,
        orientation="approximately due south",
        azimuth_degrees=180.0,
        roof_pitch_degrees=38.0,
        significant_shading="none",
        public_region="Midlands, UK",
        pvgis_loss_percent=None,
        raw_weather_dir=base / "raw" / "weather",
        processed_weather_dir=base / "processed" / "weather",
        database_path=base / "live" / "weather.sqlite",
    )


class FakeOpenMeteoClient(OpenMeteoClient):
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def get_json(self, url: str) -> dict[str, object]:
        _ = url
        return self.payload


class FakePVGISClient(PVGISClient):
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def get_json(self, url: str) -> dict[str, object]:
        _ = url
        return self.payload


def forecast_payload(times: list[str] | None = None) -> dict[str, object]:
    hourly_times = times or ["2026-07-14T10:00", "2026-07-14T11:00", "2026-07-14T12:00"]
    values = [100.0 for _ in hourly_times]
    return {
        "latitude": 52.1,
        "longitude": -1.2,
        "model": "best_match",
        "hourly": {
            "time": hourly_times,
            "shortwave_radiation": values,
            "direct_normal_irradiance": values,
            "diffuse_radiation": values,
            "global_tilted_irradiance": values,
            "cloud_cover": [50 for _ in hourly_times],
            "cloud_cover_low": [10 for _ in hourly_times],
            "cloud_cover_mid": [20 for _ in hourly_times],
            "cloud_cover_high": [30 for _ in hourly_times],
            "temperature_2m": [18 for _ in hourly_times],
            "relative_humidity_2m": [70 for _ in hourly_times],
            "precipitation": [0 for _ in hourly_times],
            "precipitation_probability": [5 for _ in hourly_times],
            "wind_speed_10m": [12 for _ in hourly_times],
            "weather_code": [1 for _ in hourly_times],
            "is_day": [1 for _ in hourly_times],
        },
        "daily": {
            "time": ["2026-07-14", "2026-10-25"],
            "sunrise": ["2026-07-14T04:58", "2026-10-25T06:45"],
            "sunset": ["2026-07-14T21:21", "2026-10-25T16:45"],
        },
    }


def archive_payload() -> dict[str, object]:
    return {
        "hourly": {
            "time": ["2026-07-13T10:00", "2026-07-13T11:00"],
            "shortwave_radiation": [100.0, 200.0],
            "direct_normal_irradiance": [80.0, 160.0],
            "diffuse_radiation": [20.0, 40.0],
            "cloud_cover": [10, 20],
            "temperature_2m": [18, 19],
            "precipitation": [0, 0],
        }
    }


def pvgis_payload() -> dict[str, object]:
    return {
        "meta": {"version": "PVGIS 5.3", "radiation_database": "PVGIS-SARAH3"},
        "outputs": {
            "monthly": {"fixed": [{"month": 1, "E_m": 100.0}, {"month": 2, "E_m": 200.0}]},
            "totals": {"fixed": {"E_y": 5800.0}},
        },
    }


def row(lead: int, forecast: float, actual: float) -> dict[str, object]:
    return {
        "source": "open_meteo",
        "model": "best_match",
        "lead_hours": lead,
        "forecast_generation_kwh": forecast,
        "actual_generation_kwh": actual,
    }


def revision(issue: str, forecast: float) -> dict[str, object]:
    return {
        "source": "open_meteo",
        "model": "best_match",
        "issue_time_utc": issue,
        "target_time_utc": "2026-07-15T12:00:00Z",
        "forecast_generation_kwh": forecast,
    }


def event(event_type: str, severity: str, message: str, observed_at: datetime):
    from weather.models import ForecastQualityEvent

    return ForecastQualityEvent(
        event_type=event_type,
        severity=severity,
        message=message,
        observed_at_utc=observed_at,
    )


if __name__ == "__main__":
    unittest.main()
