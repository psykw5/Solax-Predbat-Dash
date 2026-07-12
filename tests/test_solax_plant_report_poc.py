from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from scripts.solax_plant_report_poc import (
    ENERGY_COLUMNS,
    build_intervals,
    load_report,
    process_reports,
    validate_daily_totals,
)

HEADERS = [
    "No.",
    "Update time",
    "Daily PV Yield(kWh)",
    "Daily inverter output (kWh)",
    "Daily exported energy(kWh)",
    "Daily consumed(kWh)",
    "Daily imported energy(kWh)",
]


def write_report(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "sheetName"
    sheet.append(["Plant Report"])
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


class SolaxPlantReportPocTests(unittest.TestCase):
    def test_monotonic_daily_cumulative_values_reconstruct_final_total(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "plant-report.xlsx"
            write_report(
                path,
                [
                    [1, "2026-01-01 00:00:00", 0.0, 0.0, 0.0, 0.0, 0.0],
                    [2, "2026-01-01 00:05:00", 0.5, 0.4, 0.1, 0.3, 0.2],
                    [3, "2026-01-01 00:10:00", 0.8, 0.7, 0.2, 0.5, 0.3],
                    [4, "2026-01-02 00:00:00", 0.0, 0.0, 0.0, 0.0, 0.0],
                    [5, "2026-01-02 00:05:00", 0.1, 0.1, 0.0, 0.2, 0.1],
                ],
            )

            report = load_report(path, "test_report")
            intervals, events = build_intervals(report)
            validation = validate_daily_totals(report, intervals)

            self.assertTrue(any(event["event_type"] == "midnight_reset" for event in events))
            self.assertTrue(all(row["matches_final_cumulative"] for row in validation))
            pv_intervals = [row for row in intervals if row["field"] == "daily_pv_yield_kwh"]
            self.assertEqual(sum(row["interval_kwh"] for row in pv_intervals), 0.9)

    def test_missing_duplicate_and_negative_differences_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "plant-report.xlsx"
            write_report(
                path,
                [
                    [1, "2026-01-01 00:00:00", 0.0, 0.0, 0.0, 0.0, 0.0],
                    [2, "2026-01-01 00:10:00", 1.0, 1.0, 0.0, 0.5, 0.5],
                    [3, "2026-01-01 00:10:00", 1.0, 1.0, 0.0, 0.5, 0.5],
                    [4, "2026-01-01 00:15:00", 0.9, 1.2, 0.0, 0.7, 0.6],
                ],
            )

            report = load_report(path, "test_report")
            _, events = build_intervals(report)
            event_types = {event["event_type"] for event in events}

            self.assertIn("missing_timestamp", event_types)
            self.assertIn("duplicate_timestamp", event_types)
            self.assertIn("negative_difference", event_types)

    def test_process_reports_writes_only_to_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "raw"
            output_dir = root / "processed"
            input_dir.mkdir()
            write_report(
                input_dir / "plant-report.xlsx",
                [
                    [1, "2026-01-01 00:00:00", 0.0, 0.0, 0.0, 0.0, 0.0],
                    [2, "2026-01-01 00:05:00", 0.5, 0.4, 0.1, 0.3, 0.2],
                ],
            )

            summary = process_reports(input_dir, output_dir)

            self.assertEqual(summary["files_processed"], 1)
            self.assertTrue((output_dir / "solax_interval_energy.csv").exists())
            self.assertFalse((input_dir / "solax_interval_energy.csv").exists())
            self.assertEqual(
                set(ENERGY_COLUMNS.values())
                <= {
                    "daily_pv_yield_kwh",
                    "daily_inverter_output_kwh",
                    "daily_exported_energy_kwh",
                    "daily_consumed_kwh",
                    "daily_imported_energy_kwh",
                },
                True,
            )


if __name__ == "__main__":
    unittest.main()
