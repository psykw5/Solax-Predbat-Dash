"""Forecast evaluation metrics for transparent baselines."""

from __future__ import annotations

import math

import pandas as pd

from weather.models import ForecastEvaluationMetric

SAFE_PERCENTAGE_ACTUAL_KWH = 0.1


def evaluate_forecasts_by_lead_time(
    frame: pd.DataFrame,
    forecast_column: str = "forecast_generation_kwh",
    actual_column: str = "actual_generation_kwh",
) -> list[ForecastEvaluationMetric]:
    required = {"source", "model", "lead_hours", forecast_column, actual_column}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Evaluation frame is missing columns: {sorted(missing)}")
    data = frame.copy()
    data["lead_bucket"] = pd.to_numeric(data["lead_hours"], errors="coerce").round().astype("Int64")
    data[forecast_column] = pd.to_numeric(data[forecast_column], errors="coerce")
    data[actual_column] = pd.to_numeric(data[actual_column], errors="coerce")
    data = data.dropna(subset=["source", "model", "lead_bucket", forecast_column, actual_column])
    metrics: list[ForecastEvaluationMetric] = []
    for (source, model, lead), group in data.groupby(["source", "model", "lead_bucket"]):
        errors = group[forecast_column] - group[actual_column]
        abs_errors = errors.abs()
        squared = errors.pow(2)
        safe = group[group[actual_column] > SAFE_PERCENTAGE_ACTUAL_KWH]
        percentage_error = None
        if not safe.empty:
            percentage_error = float(
                ((safe[forecast_column] - safe[actual_column]) / safe[actual_column]).mean() * 100
            )
        metrics.append(
            ForecastEvaluationMetric(
                source=str(source),
                model=str(model),
                lead_hours=int(lead),
                sample_count=len(group),
                mae_kwh=round(float(abs_errors.mean()), 6),
                rmse_kwh=round(math.sqrt(float(squared.mean())), 6),
                mean_bias_error_kwh=round(float(errors.mean()), 6),
                mean_percentage_error=None
                if percentage_error is None
                else round(percentage_error, 6),
            )
        )
    return metrics


def daily_forecast_actual_totals(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["hour_utc"] = pd.to_datetime(data["hour_utc"])
    data["date_utc"] = data["hour_utc"].dt.date
    return (
        data.groupby(["source", "model", "date_utc"], as_index=False)[
            ["forecast_generation_kwh", "actual_generation_kwh"]
        ]
        .sum()
        .rename(
            columns={
                "forecast_generation_kwh": "daily_forecast_generation_kwh",
                "actual_generation_kwh": "daily_actual_generation_kwh",
            }
        )
    )


def forecast_revision_magnitude(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["issue_time_utc"] = pd.to_datetime(data["issue_time_utc"])
    data["target_time_utc"] = pd.to_datetime(data["target_time_utc"])
    data = data.sort_values(["source", "model", "target_time_utc", "issue_time_utc"])
    data["previous_forecast_generation_kwh"] = data.groupby(["source", "model", "target_time_utc"])[
        "forecast_generation_kwh"
    ].shift(1)
    data["revision_magnitude_kwh"] = (
        data["forecast_generation_kwh"] - data["previous_forecast_generation_kwh"]
    ).abs()
    return data.dropna(subset=["revision_magnitude_kwh"])
