from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from metrics import (
    AnnualEnergySummary,
    DailyEnergySummary,
    EnergyMetrics,
    EnergyTotal,
    MonthlyEnergySummary,
    SelfConsumptionMetric,
)


def write_processed_parquet(path: Path, frame: pd.DataFrame | None = None) -> None:
    if frame is None:
        frame = synthetic_intervals()
    frame.to_parquet(path, index=False)


def synthetic_intervals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            interval("2026-01-01 00:00:00", "2026-01-01 00:05:00", 0.5, 0.2, 0.1, 0.6, ""),
            interval(
                "2026-01-01 00:05:00",
                "2026-01-01 00:10:00",
                0.3,
                0.1,
                0.0,
                0.4,
                "imported_energy_kwh:counter_rollback",
            ),
            interval("2026-01-01 00:10:00", "2026-01-01 00:15:00", None, 0.4, 0.2, 0.7, ""),
            interval("2026-01-02 00:00:00", "2026-01-02 00:05:00", 1.0, 0.0, 0.4, 0.8, ""),
            interval("2026-02-01 00:00:00", "2026-02-01 00:05:00", 2.0, 0.5, 0.8, 1.7, ""),
        ]
    )


def interval(
    start: str,
    end: str,
    generation: float | None,
    imported: float,
    exported: float,
    consumed: float,
    quality_flags: str,
) -> dict[str, object]:
    return {
        "interval_start": pd.Timestamp(start),
        "interval_end": pd.Timestamp(end),
        "date": start[:10],
        "source_filename": "synthetic.xlsx",
        "source_file_hash": "hash",
        "pv_yield_kwh": generation,
        "inverter_output_kwh": generation,
        "exported_energy_kwh": exported,
        "consumed_energy_kwh": consumed,
        "imported_energy_kwh": imported,
        "quality_flags": quality_flags,
    }


class EnergyMetricsTests(unittest.TestCase):
    def test_total_methods_return_typed_energy_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parquet = Path(temp_dir) / "processed.parquet"
            write_processed_parquet(parquet)
            metrics = EnergyMetrics(parquet)

            generation = metrics.total_generation("2026-01-01", "2026-01-02")
            imported = metrics.total_import("2026-01-01", "2026-01-02")
            exported = metrics.total_export("2026-01-01", "2026-01-02")
            consumed = metrics.total_consumption("2026-01-01", "2026-01-02")

            self.assertIsInstance(generation, EnergyTotal)
            self.assertIsInstance(generation, BaseModel)
            self.assertEqual(generation.kwh, 0.8)
            self.assertEqual(imported.kwh, 0.7)
            self.assertEqual(exported.kwh, 0.3)
            self.assertEqual(consumed.kwh, 1.7)
            self.assertEqual(generation.interval_count, 3)
            self.assertEqual(generation.quality_flagged_interval_count, 1)

    def test_self_consumption_returns_typed_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parquet = Path(temp_dir) / "processed.parquet"
            write_processed_parquet(parquet)
            result = EnergyMetrics(parquet).self_consumption("2026-01-01", "2026-01-02")

            self.assertIsInstance(result, SelfConsumptionMetric)
            self.assertEqual(result.generation_kwh, 0.8)
            self.assertEqual(result.export_kwh, 0.3)
            self.assertEqual(result.self_consumed_kwh, 0.5)
            self.assertEqual(result.self_consumption_ratio, 0.625)
            self.assertEqual(result.self_consumption_percent, 62.5)

    def test_self_consumption_is_none_when_generation_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parquet = Path(temp_dir) / "processed.parquet"
            frame = synthetic_intervals()
            frame.loc[:, "pv_yield_kwh"] = 0.0
            frame.loc[:, "exported_energy_kwh"] = 0.0
            write_processed_parquet(parquet, frame)
            result = EnergyMetrics(parquet).self_consumption("2026-01-01", "2026-01-02")

            self.assertIsNone(result.self_consumption_ratio)
            self.assertIsNone(result.self_consumption_percent)
            self.assertEqual(result.self_consumed_kwh, 0.0)

    def test_daily_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parquet = Path(temp_dir) / "processed.parquet"
            write_processed_parquet(parquet)
            summary = EnergyMetrics(parquet).daily_summary(date(2026, 1, 1))

            self.assertIsInstance(summary, DailyEnergySummary)
            self.assertEqual(summary.date, date(2026, 1, 1))
            self.assertEqual(summary.generation_kwh, 0.8)
            self.assertEqual(summary.import_kwh, 0.7)
            self.assertEqual(summary.export_kwh, 0.3)
            self.assertEqual(summary.consumption_kwh, 1.7)
            self.assertEqual(summary.interval_count, 3)
            self.assertEqual(summary.quality_flagged_interval_count, 1)

    def test_monthly_summary_includes_daily_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parquet = Path(temp_dir) / "processed.parquet"
            write_processed_parquet(parquet)
            summary = EnergyMetrics(parquet).monthly_summary(2026, 1)

            self.assertIsInstance(summary, MonthlyEnergySummary)
            self.assertEqual(summary.year, 2026)
            self.assertEqual(summary.month, 1)
            self.assertEqual(summary.generation_kwh, 1.8)
            self.assertEqual(summary.import_kwh, 0.7)
            self.assertEqual(summary.export_kwh, 0.7)
            self.assertEqual(summary.consumption_kwh, 2.5)
            self.assertEqual(summary.interval_count, 4)
            self.assertEqual(len(summary.days), 31)
            self.assertEqual(summary.days[0].date, date(2026, 1, 1))
            self.assertEqual(summary.days[1].generation_kwh, 1.0)

    def test_annual_summary_includes_monthly_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parquet = Path(temp_dir) / "processed.parquet"
            write_processed_parquet(parquet)
            summary = EnergyMetrics(parquet).annual_summary(2026)

            self.assertIsInstance(summary, AnnualEnergySummary)
            self.assertEqual(summary.year, 2026)
            self.assertEqual(summary.generation_kwh, 3.8)
            self.assertEqual(summary.import_kwh, 1.2)
            self.assertEqual(summary.export_kwh, 1.5)
            self.assertEqual(summary.consumption_kwh, 4.2)
            self.assertEqual(summary.interval_count, 5)
            self.assertEqual(len(summary.months), 12)
            self.assertEqual(summary.months[0].generation_kwh, 1.8)
            self.assertEqual(summary.months[1].generation_kwh, 2.0)

    def test_range_is_start_inclusive_and_end_exclusive_by_full_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parquet = Path(temp_dir) / "processed.parquet"
            write_processed_parquet(parquet)
            result = EnergyMetrics(parquet).total_generation(
                datetime(2026, 1, 1, 0, 5),
                datetime(2026, 1, 1, 0, 15),
            )

            self.assertEqual(result.kwh, 0.3)
            self.assertEqual(result.interval_count, 2)

    def test_invalid_range_and_month_raise_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parquet = Path(temp_dir) / "processed.parquet"
            write_processed_parquet(parquet)
            metrics = EnergyMetrics(parquet)

            with self.assertRaises(ValueError):
                metrics.total_generation("2026-01-02", "2026-01-01")
            with self.assertRaises(ValueError):
                metrics.monthly_summary(2026, 13)

    def test_missing_file_and_missing_schema_columns_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.parquet"
            with self.assertRaises(FileNotFoundError):
                EnergyMetrics(missing).total_generation("2026-01-01", "2026-01-02")

            bad = Path(temp_dir) / "bad.parquet"
            pd.DataFrame({"interval_start": [pd.Timestamp("2026-01-01")]}).to_parquet(
                bad, index=False
            )
            with self.assertRaises(ValueError):
                EnergyMetrics(bad).total_generation("2026-01-01", "2026-01-02")


if __name__ == "__main__":
    unittest.main()
