from __future__ import annotations

import unittest
from datetime import UTC, datetime

import pandas as pd

from transforms.financial import (
    aggregate_solax_to_settlement_half_hours,
    annotate_export_agreement_status,
    build_lifetime_summary,
    calculate_financial_values,
    join_financial_rates,
    prepare_active_rates,
    validate_agreements,
    validate_rates,
)
from transforms.solax_intervals import cumulative_to_intervals


def energy_rows(starts: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "interval_start": pd.to_datetime(starts),
            "interval_end": pd.to_datetime(starts) + pd.Timedelta(minutes=5),
            "pv_yield_kwh": [0.3] * len(starts),
            "exported_energy_kwh": [0.1] * len(starts),
            "consumed_energy_kwh": [0.2] * len(starts),
            "imported_energy_kwh": [0.0] * len(starts),
            "quality_flags": [""] * len(starts),
        }
    )


def rate_frame(direction: str, value: float, start: str, end: str | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "agreement_id": [f"{direction}_agreement"],
            "direction": [direction],
            "tariff_code": [f"{direction.upper()}-TARIFF"],
            "product_code": [f"{direction.upper()}-PRODUCT"],
            "rate_type": ["standard-unit-rates"],
            "value_inc_vat": [value],
            "valid_from": [start],
            "valid_to": [end],
            "payment_method": [None],
            "source_endpoint": ["https://example.test/rates"],
            "ingestion_timestamp": [datetime.now(UTC).isoformat()],
        }
    )


class FinancialTransformTests(unittest.TestCase):
    def test_import_and_export_rates_can_differ_in_same_half_hour(self) -> None:
        solax = energy_rows(
            [
                "2023-02-01 12:00:00",
                "2023-02-01 12:05:00",
                "2023-02-01 12:10:00",
                "2023-02-01 12:15:00",
                "2023-02-01 12:20:00",
                "2023-02-01 12:25:00",
            ]
        )
        half_hourly, quality = aggregate_solax_to_settlement_half_hours(solax)
        joined = join_financial_rates(
            half_hourly,
            rate_frame("import", 30.0, "2023-02-01T00:00:00Z", "2023-02-02T00:00:00Z"),
            rate_frame("export", 15.0, "2023-02-01T00:00:00Z", "2023-02-02T00:00:00Z"),
        )
        calculated, join_quality = calculate_financial_values(joined)
        summary = build_lifetime_summary(calculated)

        self.assertTrue(quality.empty)
        self.assertTrue(join_quality[join_quality["severity"].isin(["warning", "error"])].empty)
        self.assertAlmostEqual(calculated.loc[0, "generation_kwh"], 1.8)
        self.assertAlmostEqual(calculated.loc[0, "export_kwh"], 0.6)
        self.assertAlmostEqual(calculated.loc[0, "estimated_self_consumed_solar_kwh"], 1.2)
        self.assertAlmostEqual(calculated.loc[0, "avoided_import_value"], 0.36)
        self.assertAlmostEqual(calculated.loc[0, "export_income"], 0.09)
        self.assertEqual(summary.confirmed_financial_benefit, 0.45)

    def test_tariff_change_selects_applicable_rate(self) -> None:
        solax = energy_rows(["2023-02-01 00:00:00", "2023-02-01 00:30:00"])
        half_hourly, _ = aggregate_solax_to_settlement_half_hours(solax)
        import_rates = pd.concat(
            [
                rate_frame("import", 20.0, "2023-02-01T00:00:00Z", "2023-02-01T00:30:00Z"),
                rate_frame("import", 40.0, "2023-02-01T00:30:00Z", "2023-02-02T00:00:00Z"),
            ],
            ignore_index=True,
        )
        joined = join_financial_rates(
            half_hourly,
            import_rates,
            rate_frame("export", 10.0, "2023-02-01T00:00:00Z", "2023-02-02T00:00:00Z"),
        )

        self.assertEqual(joined["import_rate_inc_vat"].tolist(), [20.0, 40.0])

    def test_missing_rate_interval_is_excluded(self) -> None:
        solax = energy_rows(["2023-02-01 12:00:00"])
        half_hourly, _ = aggregate_solax_to_settlement_half_hours(solax)
        joined = join_financial_rates(
            half_hourly,
            rate_frame("import", 30.0, "2023-02-01T00:00:00Z", "2023-02-01T01:00:00Z"),
            rate_frame("export", 15.0, "2023-02-01T00:00:00Z", "2023-02-01T01:00:00Z"),
        )
        calculated, quality = calculate_financial_values(joined)

        self.assertFalse(calculated.loc[0, "included_in_financials"])
        self.assertTrue(
            {
                "half_hours_without_import_rate",
                "half_hours_without_export_rate",
                "tariff_coverage_gap",
                "export_statement_reconciliation_not_performed",
            }.issubset(set(quality["event_type"]))
        )

    def test_overlapping_agreements_are_flagged(self) -> None:
        agreements = pd.DataFrame(
            {
                "meter_point_id": ["mp_1", "mp_1"],
                "direction": ["import", "import"],
                "valid_from": ["2023-01-01T00:00:00Z", "2023-01-15T00:00:00Z"],
                "valid_to": ["2023-02-01T00:00:00Z", "2023-03-01T00:00:00Z"],
            }
        )

        quality = validate_agreements(agreements)

        self.assertIn("overlapping_tariff_agreements", quality["event_type"].tolist())

    def test_overlapping_rates_are_flagged(self) -> None:
        rates = pd.concat(
            [
                rate_frame("import", 20.0, "2023-01-01T00:00:00Z", "2023-01-02T00:00:00Z"),
                rate_frame("import", 25.0, "2023-01-01T12:00:00Z", "2023-01-03T00:00:00Z"),
            ],
            ignore_index=True,
        )

        quality = validate_rates(rates, "import")

        self.assertIn("overlapping_import_rates", quality["event_type"].tolist())

    def test_duplicate_payment_method_rates_choose_direct_debit(self) -> None:
        solax = energy_rows(["2023-04-01 00:00:00"])
        half_hourly, _ = aggregate_solax_to_settlement_half_hours(solax)
        agreements = pd.DataFrame(
            {
                "agreement_id": ["import_agreement", "export_agreement"],
                "direction": ["import", "export"],
                "valid_from": ["2023-03-31T23:00:00Z", "2023-03-31T23:00:00Z"],
                "valid_to": ["2023-06-07T23:00:00Z", None],
            }
        )
        import_rates = pd.DataFrame(
            {
                "agreement_id": ["import_agreement", "import_agreement"],
                "direction": ["import", "import"],
                "tariff_code": ["IMPORT-TARIFF", "IMPORT-TARIFF"],
                "product_code": ["IMPORT-PRODUCT", "IMPORT-PRODUCT"],
                "rate_type": ["standard-unit-rates", "standard-unit-rates"],
                "value_inc_vat": [49.798350, 50.488095],
                "valid_from": ["2023-03-31T23:00:00Z", "2023-03-31T23:00:00Z"],
                "valid_to": ["2023-06-30T23:00:00Z", "2023-06-30T23:00:00Z"],
                "payment_method": ["DIRECT_DEBIT", "NON_DIRECT_DEBIT"],
                "source_endpoint": ["https://example.test/rates", "https://example.test/rates"],
                "ingestion_timestamp": [
                    datetime.now(UTC).isoformat(),
                    datetime.now(UTC).isoformat(),
                ],
            }
        )
        prepared = prepare_active_rates(import_rates, agreements, "import")
        half_hourly = annotate_export_agreement_status(half_hourly, agreements)
        joined = join_financial_rates(
            half_hourly,
            prepared,
            rate_frame("export", 10.0, "2023-03-31T23:00:00Z", None),
        )

        self.assertEqual(joined.loc[0, "import_rate_inc_vat"], 49.798350)

    def test_zero_duration_daily_baseline_is_excluded_from_canonical_intervals(self) -> None:
        raw = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2023-01-01 00:00:00", "2023-01-01 00:05:00"]),
                "source_filename": ["source.xlsx", "source.xlsx"],
                "source_file_hash": ["hash", "hash"],
                "Daily PV Yield(kWh)": [0.0, 0.2],
                "Daily inverter output (kWh)": [0.0, 0.2],
                "Daily exported energy(kWh)": [0.0, 0.1],
                "Daily consumed(kWh)": [1.5, 1.7],
                "Daily imported energy(kWh)": [0.0, 0.0],
            }
        )

        canonical, quality = cumulative_to_intervals(raw)

        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical.iloc[0]["interval_start"], pd.Timestamp("2023-01-01 00:00:00"))
        self.assertEqual(canonical.iloc[0]["interval_end"], pd.Timestamp("2023-01-01 00:05:00"))
        self.assertIn("zero_duration_daily_baseline", quality["event_type"].tolist())

    def test_bst_start_nonexistent_local_time_is_flagged(self) -> None:
        solax = energy_rows(["2023-03-26 01:00:00"])
        half_hourly, quality = aggregate_solax_to_settlement_half_hours(solax)

        self.assertTrue(half_hourly.empty)
        self.assertIn("dst_transition_interval", quality["event_type"].tolist())

    def test_bst_end_ambiguous_local_time_is_flagged(self) -> None:
        solax = energy_rows(["2023-10-29 01:00:00"])
        half_hourly, quality = aggregate_solax_to_settlement_half_hours(solax)

        self.assertTrue(half_hourly.empty)
        self.assertIn("dst_transition_interval", quality["event_type"].tolist())


if __name__ == "__main__":
    unittest.main()
