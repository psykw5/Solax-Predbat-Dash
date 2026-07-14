"""Read-only energy metrics over processed interval Parquet."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from .models import (
    AnnualEnergySummary,
    DailyEnergySummary,
    EnergyTotal,
    MonthlyEnergySummary,
    SelfConsumptionMetric,
)

DEFAULT_PARQUET_PATH = Path("data/processed/solax/solax_intervals.parquet")

GENERATION_COLUMN = "pv_yield_kwh"
IMPORT_COLUMN = "imported_energy_kwh"
EXPORT_COLUMN = "exported_energy_kwh"
REPORTED_CONSUMPTION_COLUMN = "consumed_energy_kwh"

REQUIRED_COLUMNS = {
    "interval_start",
    "interval_end",
    "quality_flags",
    GENERATION_COLUMN,
    IMPORT_COLUMN,
    EXPORT_COLUMN,
    REPORTED_CONSUMPTION_COLUMN,
}


class EnergyMetrics:
    """Simple metrics API backed only by processed interval Parquet.

    Time range methods use start-inclusive, end-exclusive semantics. Only
    intervals fully contained in the query window are included.
    """

    def __init__(self, parquet_path: Path | str = DEFAULT_PARQUET_PATH) -> None:
        self.parquet_path = Path(parquet_path)
        self._data: pd.DataFrame | None = None

    def total_generation(self, start: datetime | str, end: datetime | str) -> EnergyTotal:
        return self._total("total_generation", GENERATION_COLUMN, start, end)

    def total_import(self, start: datetime | str, end: datetime | str) -> EnergyTotal:
        return self._total("total_import", IMPORT_COLUMN, start, end)

    def total_export(self, start: datetime | str, end: datetime | str) -> EnergyTotal:
        return self._total("total_export", EXPORT_COLUMN, start, end)

    def total_household_consumption(
        self, start: datetime | str, end: datetime | str
    ) -> EnergyTotal:
        start_dt, end_dt = normalize_range(start, end)
        window = self._window(start_dt, end_dt)
        return EnergyTotal(
            metric="total_household_consumption",
            start=start_dt,
            end=end_dt,
            kwh=round(derived_consumption_kwh(window), 6),
            interval_count=len(window),
            quality_flagged_interval_count=count_quality_flags(window),
        )

    def total_consumption(self, start: datetime | str, end: datetime | str) -> EnergyTotal:
        """Backward-compatible alias for canonical household consumption."""
        return self.total_household_consumption(start, end)

    def total_reported_inverter_consumption(
        self, start: datetime | str, end: datetime | str
    ) -> EnergyTotal:
        return self._total(
            "total_reported_inverter_consumption",
            REPORTED_CONSUMPTION_COLUMN,
            start,
            end,
        )

    def self_consumption(self, start: datetime | str, end: datetime | str) -> SelfConsumptionMetric:
        start_dt, end_dt = normalize_range(start, end)
        window = self._window(start_dt, end_dt)
        generation = safe_sum(window[GENERATION_COLUMN])
        export = safe_sum(window[EXPORT_COLUMN])
        self_consumed = max(generation - export, 0.0)
        ratio = None if generation == 0 else min(max(self_consumed / generation, 0.0), 1.0)
        return SelfConsumptionMetric(
            start=start_dt,
            end=end_dt,
            generation_kwh=round(generation, 6),
            export_kwh=round(export, 6),
            self_consumed_kwh=round(self_consumed, 6),
            self_consumption_ratio=None if ratio is None else round(ratio, 6),
            self_consumption_percent=None if ratio is None else round(ratio * 100, 4),
            interval_count=len(window),
            quality_flagged_interval_count=count_quality_flags(window),
        )

    def daily_summary(self, summary_date: date | datetime | str) -> DailyEnergySummary:
        day = normalize_date(summary_date)
        start = datetime.combine(day, time.min)
        end = start + timedelta(days=1)
        return self._summary_for_day(day, start, end)

    def monthly_summary(self, year: int, month: int) -> MonthlyEnergySummary:
        if month < 1 or month > 12:
            raise ValueError("month must be between 1 and 12")
        start = datetime(year, month, 1)
        _, days_in_month = calendar.monthrange(year, month)
        end = start + timedelta(days=days_in_month)
        window = self._window(start, end)
        totals = self._summary_values(window)
        days = tuple(
            self.daily_summary(date(year, month, day_number))
            for day_number in range(1, days_in_month + 1)
        )
        return MonthlyEnergySummary(
            year=year,
            month=month,
            start=start,
            end=end,
            days=days,
            **totals,
        )

    def annual_summary(self, year: int) -> AnnualEnergySummary:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        window = self._window(start, end)
        totals = self._summary_values(window)
        months = tuple(self.monthly_summary(year, month) for month in range(1, 13))
        return AnnualEnergySummary(
            year=year,
            start=start,
            end=end,
            months=months,
            **totals,
        )

    def coverage_range(self) -> tuple[datetime, datetime]:
        """Return the first interval start and last interval end in the dataset."""
        data = self._load()
        if data.empty:
            raise ValueError("Processed interval parquet contains no intervals.")
        return (
            data["interval_start"].min().to_pydatetime(),
            data["interval_end"].max().to_pydatetime(),
        )

    def _total(
        self,
        metric: str,
        column: str,
        start: datetime | str,
        end: datetime | str,
    ) -> EnergyTotal:
        start_dt, end_dt = normalize_range(start, end)
        window = self._window(start_dt, end_dt)
        return EnergyTotal(
            metric=metric,
            start=start_dt,
            end=end_dt,
            kwh=round(safe_sum(window[column]), 6),
            interval_count=len(window),
            quality_flagged_interval_count=count_quality_flags(window),
        )

    def _summary_for_day(self, day: date, start: datetime, end: datetime) -> DailyEnergySummary:
        window = self._window(start, end)
        totals = self._summary_values(window)
        return DailyEnergySummary(date=day, start=start, end=end, **totals)

    def _summary_values(self, window: pd.DataFrame) -> dict[str, float | int | None]:
        generation = safe_sum(window[GENERATION_COLUMN])
        exported = safe_sum(window[EXPORT_COLUMN])
        self_consumed = max(generation - exported, 0.0)
        ratio = None if generation == 0 else min(max(self_consumed / generation, 0.0), 1.0)
        return {
            "generation_kwh": round(generation, 6),
            "import_kwh": round(safe_sum(window[IMPORT_COLUMN]), 6),
            "export_kwh": round(exported, 6),
            "household_consumption_kwh": round(derived_consumption_kwh(window), 6),
            "reported_inverter_consumption_kwh": round(
                safe_sum(window[REPORTED_CONSUMPTION_COLUMN]), 6
            ),
            "self_consumed_kwh": round(self_consumed, 6),
            "self_consumption_ratio": None if ratio is None else round(ratio, 6),
            "self_consumption_percent": None if ratio is None else round(ratio * 100, 4),
            "interval_count": len(window),
            "quality_flagged_interval_count": count_quality_flags(window),
        }

    def _window(self, start: datetime, end: datetime) -> pd.DataFrame:
        data = self._load()
        return data[(data["interval_start"] >= start) & (data["interval_end"] <= end)].copy()

    def _load(self) -> pd.DataFrame:
        if self._data is None:
            if not self.parquet_path.exists():
                raise FileNotFoundError(
                    f"Processed interval parquet not found: {self.parquet_path}"
                )
            data = pd.read_parquet(self.parquet_path)
            missing = REQUIRED_COLUMNS - set(data.columns)
            if missing:
                raise ValueError(
                    f"Processed interval parquet is missing required columns: {sorted(missing)}"
                )
            data = data.copy()
            data["interval_start"] = pd.to_datetime(data["interval_start"])
            data["interval_end"] = pd.to_datetime(data["interval_end"])
            numeric_columns = [
                GENERATION_COLUMN,
                IMPORT_COLUMN,
                EXPORT_COLUMN,
                REPORTED_CONSUMPTION_COLUMN,
            ]
            for column in numeric_columns:
                data[column] = pd.to_numeric(data[column], errors="coerce")
            data["quality_flags"] = data["quality_flags"].fillna("").astype(str)
            self._data = data.sort_values(["interval_start", "interval_end"]).reset_index(drop=True)
        return self._data


def normalize_range(start: datetime | str, end: datetime | str) -> tuple[datetime, datetime]:
    start_dt = normalize_datetime(start)
    end_dt = normalize_datetime(end)
    if end_dt <= start_dt:
        raise ValueError("end must be after start")
    return start_dt, end_dt


def normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert(None)
    return parsed.to_pydatetime()


def normalize_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def safe_sum(series: pd.Series) -> float:
    total = series.dropna().sum()
    if pd.isna(total):
        return 0.0
    return float(total)


def count_quality_flags(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    return int(frame["quality_flags"].fillna("").astype(str).str.len().gt(0).sum())


def derived_consumption_kwh(frame: pd.DataFrame) -> float:
    """Canonical household consumption derived from reconciled PV/export/import flows."""
    generation = safe_sum(frame[GENERATION_COLUMN])
    exported = safe_sum(frame[EXPORT_COLUMN])
    imported = safe_sum(frame[IMPORT_COLUMN])
    return max(generation - exported, 0.0) + imported
