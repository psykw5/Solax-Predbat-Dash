"""CLI for Wattson weather and solar forecast collection."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from weather.config import load_weather_config, redacted_location
from weather.evaluation import evaluate_forecasts_by_lead_time
from weather.pipeline import (
    SOLA_X_HISTORY_START,
    build_evaluation_dataset,
    build_pvgis_baseline,
    collect_forecast,
    collect_historical_forecast_backfill,
    collect_observations,
    collect_satellite_radiation,
    write_processed_weather,
)
from weather.store import WeatherStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Wattson weather and solar forecast collection.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect-forecast")
    subparsers.add_parser("collect-observations")
    subparsers.add_parser("backfill-history")
    subparsers.add_parser("build-pvgis-baseline")
    subparsers.add_parser("build-evaluation-dataset")
    subparsers.add_parser("evaluate-baselines")
    subparsers.add_parser("run")
    args = parser.parse_args()
    config = load_weather_config()
    store = WeatherStore(config.database_path)
    try:
        if args.command == "collect-forecast":
            run, intervals = collect_forecast(config=config, store=store)
            print(
                json.dumps(
                    {
                        "collector": "weather_forecast",
                        "status": "success",
                        "region": redacted_location(config)["region"],
                        "intervals": len(intervals),
                        "model": run.model,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command in {"collect-observations", "backfill-history"}:
            rows = collect_observations(config=config, store=store, start=SOLA_X_HISTORY_START)
            if args.command == "backfill-history":
                collect_historical_forecast_backfill(
                    SOLA_X_HISTORY_START, date.today(), config=config, store=store
                )
                collect_satellite_radiation(
                    SOLA_X_HISTORY_START, date.today(), config=config, store=store
                )
            print(
                json.dumps(
                    {
                        "collector": "weather_observations",
                        "status": "success",
                        "region": redacted_location(config)["region"],
                        "intervals": len(rows),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "build-pvgis-baseline":
            baseline = build_pvgis_baseline(config=config, store=store)
            print(
                json.dumps(
                    {
                        "collector": "pvgis",
                        "status": "success",
                        "region": baseline.public_region,
                        "annual_expected_generation_kwh": baseline.annual_expected_generation_kwh,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "build-evaluation-dataset":
            frame = build_from_store(store, config)
            print(json.dumps({"rows": len(frame), "status": "success"}, sort_keys=True))
            return 0
        if args.command == "evaluate-baselines":
            frame = build_from_store(store, config)
            if "forecast_generation_kwh" not in frame:
                frame["forecast_generation_kwh"] = (
                    frame["global_horizontal_irradiance_w_m2"].fillna(0)
                    / 1000.0
                    * config.array_capacity_kwp
                )
            metrics = evaluate_forecasts_by_lead_time(frame)
            print(json.dumps([metric.model_dump() for metric in metrics], default=str))
            return 0
        if args.command == "run":
            now = datetime.now(UTC)
            last_forecast = latest_completed(store, "weather_forecast")
            if last_forecast is None or now - last_forecast >= timedelta(hours=3):
                collect_forecast(config=config, store=store, now=now)
            last_observation = latest_completed(store, "weather_observations")
            if last_observation is None or now - last_observation >= timedelta(days=1):
                collect_observations(
                    config=config,
                    store=store,
                    start=max(SOLA_X_HISTORY_START, date.today() - timedelta(days=7)),
                    now=now,
                )
            write_processed_weather(store, config.processed_weather_dir)
            print(json.dumps({"collector": "weather", "status": "success"}, sort_keys=True))
            return 0
    finally:
        store.close()
    return 1


def build_from_store(store: WeatherStore, config: object):
    forecast = store.table_frame("weather_forecast_interval")
    observations = store.table_frame("weather_observation_interval")
    frame = build_evaluation_dataset(forecast, observation_frame=observations)
    output = Path("data/processed/weather/weather_generation_evaluation.parquet")
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output, index=False)
    _ = config
    return frame


def latest_completed(store: WeatherStore, collector: str) -> datetime | None:
    _ = collector
    row = store.connection.execute(
        "select max(retrieved_at_utc) as completed from weather_forecast_run"
    ).fetchone()
    if row is None or row["completed"] is None:
        return None
    return datetime.fromisoformat(row["completed"])
