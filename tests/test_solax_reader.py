from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from ingestion.solax_reader import read_plant_report

STANDARD_HEADERS = [
    "No.",
    "Update time",
    "Daily PV Yield(kWh)",
    "Daily inverter output (kWh)",
    "Daily exported energy(kWh)",
    "Daily consumed(kWh)",
    "Daily imported energy(kWh)",
]


def write_workbook(path: Path, headers: list[str | None]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "sheetName"
    sheet.append(["Plant Report"])
    sheet.append(headers)
    sheet.append([1, "2026-04-03 00:00:00", 37.2, 40.0, 0.0, 40.08, 0.08])
    sheet.append([2, "2026-04-03 00:05:00", 37.2, 0.0, 0.0, 0.08, 0.08])
    workbook.save(path)


class SolaxReaderTests(unittest.TestCase):
    def test_reads_standard_header_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "standard.xlsx"
            write_workbook(path, STANDARD_HEADERS)

            metadata, frame = read_plant_report(path)

            self.assertEqual(metadata.row_count, 2)
            self.assertEqual(
                frame["timestamp"].iloc[0].strftime("%Y-%m-%d %H:%M:%S"), "2026-04-03 00:00:00"
            )
            self.assertEqual(frame["Daily PV Yield(kWh)"].iloc[0], 37.2)

    def test_reads_blank_header_known_layout_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "blank-header.xlsx"
            write_workbook(path, [None] * len(STANDARD_HEADERS))

            metadata, frame = read_plant_report(path)

            self.assertEqual(metadata.row_count, 2)
            self.assertEqual(
                frame["timestamp"].iloc[1].strftime("%Y-%m-%d %H:%M:%S"), "2026-04-03 00:05:00"
            )
            self.assertEqual(frame["Daily imported energy(kWh)"].iloc[0], 0.08)


if __name__ == "__main__":
    unittest.main()
