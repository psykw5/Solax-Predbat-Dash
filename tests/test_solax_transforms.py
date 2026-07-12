from __future__ import annotations

import unittest

import pandas as pd

from transforms.solax_intervals import consolidate_canonical, cumulative_to_intervals
from validation.reports import build_daily_validation
from validation.solax_quality import detect_missing_timestamps


def synthetic_raw() -> pd.DataFrame:
    return pd.DataFrame(
        [
            row("report_a.xlsx", "2026-01-01 00:00:00", 0.0, 0.0, 0.0, 0.0, 0.0),
            row("report_a.xlsx", "2026-01-01 00:05:00", 0.5, 0.4, 0.1, 0.3, 0.2),
            row("report_a.xlsx", "2026-01-01 00:10:00", 0.8, 0.7, 0.2, 0.5, 0.3),
            row("report_a.xlsx", "2026-01-02 00:00:00", 0.0, 0.0, 0.0, 0.0, 0.0),
            row("report_a.xlsx", "2026-01-02 00:05:00", 0.1, 0.1, 0.0, 0.2, 0.1),
        ]
    )


def row(
    source_filename: str,
    timestamp: str,
    pv: float,
    inverter: float,
    exported: float,
    consumed: float,
    imported: float,
) -> dict[str, object]:
    return {
        "source_filename": source_filename,
        "source_file_hash": f"hash-{source_filename}",
        "timestamp": pd.Timestamp(timestamp),
        "Daily PV Yield(kWh)": pv,
        "Daily inverter output (kWh)": inverter,
        "Daily exported energy(kWh)": exported,
        "Daily consumed(kWh)": consumed,
        "Daily imported energy(kWh)": imported,
    }


class SolaxTransformTests(unittest.TestCase):
    def test_cumulative_to_intervals_reconstructs_daily_totals(self) -> None:
        canonical, events = cumulative_to_intervals(synthetic_raw())
        validation = build_daily_validation(canonical, synthetic_raw())

        self.assertFalse(canonical.empty)
        self.assertTrue(validation["matches_final_cumulative"].all())
        self.assertIn("midnight_reset", set(events["event_type"]))
        self.assertAlmostEqual(canonical["pv_yield_kwh"].sum(), 0.9)

    def test_duplicate_and_counter_rollback_are_flagged(self) -> None:
        raw = pd.DataFrame(
            [
                row("report_a.xlsx", "2026-01-01 00:00:00", 0.0, 0.0, 0.0, 0.0, 0.0),
                row("report_a.xlsx", "2026-01-01 00:05:00", 1.0, 1.0, 0.0, 0.5, 0.5),
                row("report_a.xlsx", "2026-01-01 00:05:00", 1.0, 1.0, 0.0, 0.5, 0.5),
                row("report_a.xlsx", "2026-01-01 00:10:00", 0.9, 1.2, 0.0, 0.7, 0.6),
            ]
        )

        canonical, events = cumulative_to_intervals(raw)
        event_types = set(events["event_type"])

        self.assertIn("duplicate_interval", event_types)
        self.assertIn("counter_rollback", event_types)
        rollback_rows = canonical[
            canonical["quality_flags"].str.contains("counter_rollback", na=False)
        ]
        self.assertEqual(len(rollback_rows), 1)
        self.assertTrue(pd.isna(rollback_rows.iloc[0]["pv_yield_kwh"]))

    def test_missing_timestamp_detection(self) -> None:
        raw = pd.DataFrame(
            [
                row("report_a.xlsx", "2026-01-01 00:00:00", 0.0, 0.0, 0.0, 0.0, 0.0),
                row("report_a.xlsx", "2026-01-01 00:10:00", 1.0, 1.0, 0.0, 0.5, 0.5),
            ]
        )

        events = detect_missing_timestamps(raw)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "missing_timestamp")

    def test_overlapping_intervals_consolidate_to_one_row(self) -> None:
        raw = pd.concat(
            [
                synthetic_raw(),
                synthetic_raw().assign(
                    source_filename="report_b.xlsx", source_file_hash="hash-report_b.xlsx"
                ),
            ],
            ignore_index=True,
        )
        canonical, _ = cumulative_to_intervals(raw)
        consolidated, events = consolidate_canonical(canonical)

        self.assertLess(len(consolidated), len(canonical))
        self.assertEqual(
            len(consolidated),
            canonical[["interval_start", "interval_end"]].drop_duplicates().shape[0],
        )
        self.assertIn("overlapping_interval", set(events["event_type"]))
        self.assertTrue(
            consolidated["quality_flags"].str.contains("overlapping_interval", na=False).any()
        )


if __name__ == "__main__":
    unittest.main()
