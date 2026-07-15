from __future__ import annotations

import unittest
from datetime import UTC, datetime

import pandas as pd

from tariffs.models import BatteryAssumptions, TariffScenario
from tariffs.real_comparison import (
    build_public_summary,
    monthly_replay_rows,
    representative_12_month_period,
    validate_public_summary,
)
from tariffs.whatif import (
    actual_flow_replay,
    default_scenarios,
    future_ev_scenario,
    optimise_battery_dispatch,
    prime_export_window_mask,
    validate_rate_tables,
    validate_scenario_pairing,
)


class TariffWhatIfTests(unittest.TestCase):
    def test_dynamic_import_and_export_rates(self) -> None:
        result = actual_flow_replay(
            measured(), scenario(), import_rates([10, 40]), export_rates([5, 25])
        )

        self.assertEqual(result.import_energy_cost_gbp, 0.5)
        self.assertEqual(result.export_income_gbp, 0.3)

    def test_flat_export_rates(self) -> None:
        result = actual_flow_replay(
            measured(), scenario(), import_rates([20, 20]), export_rates([15, 15])
        )

        self.assertEqual(result.export_income_gbp, 0.3)

    def test_prime_export_window_boundaries(self) -> None:
        stamps = pd.Series(
            pd.to_datetime(
                ["2026-01-14T10:30:00Z", "2026-01-14T11:00:00Z", "2026-01-14T16:00:00Z"],
                utc=True,
            )
        )

        self.assertEqual(prime_export_window_mask(stamps).tolist(), [False, True, False])

    def test_standing_charges(self) -> None:
        result = actual_flow_replay(
            measured(),
            scenario(standing_charge_p_per_day=50),
            import_rates([20, 20]),
            export_rates([0, 0]),
        )

        self.assertEqual(result.standing_charges_gbp, 0.5)

    def test_missing_and_overlapping_rates(self) -> None:
        events = validate_rate_tables(overlapping_rates(), "scenario", "import")

        self.assertTrue(any(event.event_type == "overlapping_import_rates" for event in events))

    def test_incompatible_tariff_pairing(self) -> None:
        event = validate_scenario_pairing(scenario(eligibility_status="compatibility_unconfirmed"))

        self.assertIsNotNone(event)
        self.assertEqual(event.severity, "warning")

    def test_battery_capacity_and_power_limits(self) -> None:
        simulated, result = optimise_battery_dispatch(
            measured_for_battery(),
            scenario(),
            import_rates([5, 50, 50, 5], rows=4),
            export_rates([5, 5, 5, 5], rows=4),
            BatteryAssumptions(
                usable_capacity_kwh=1,
                maximum_soc_kwh=1,
                initial_soc_kwh=0.5,
                charge_power_kw=1,
                discharge_power_kw=1,
            ),
        )

        self.assertLessEqual(simulated["battery_soc_kwh"].max(), 1)
        self.assertGreater(result.battery_throughput_kwh, 0)

    def test_charging_and_discharging_efficiency(self) -> None:
        _, efficient = optimise_battery_dispatch(
            measured_for_battery(),
            scenario(),
            import_rates([5, 50, 50, 5], rows=4),
            export_rates([5, 5, 5, 5], rows=4),
            BatteryAssumptions(charge_efficiency=1, discharge_efficiency=1),
        )
        _, lossy = optimise_battery_dispatch(
            measured_for_battery(),
            scenario(),
            import_rates([5, 50, 50, 5], rows=4),
            export_rates([5, 5, 5, 5], rows=4),
            BatteryAssumptions(charge_efficiency=0.8, discharge_efficiency=0.8),
        )

        self.assertLessEqual(efficient.net_electricity_cost_gbp, lossy.net_electricity_cost_gbp)

    def test_minimum_state_of_charge(self) -> None:
        simulated, _ = optimise_battery_dispatch(
            measured_for_battery(),
            scenario(),
            import_rates([5, 50, 50, 5], rows=4),
            export_rates([5, 5, 5, 5], rows=4),
            BatteryAssumptions(minimum_soc_kwh=2, initial_soc_kwh=2, maximum_soc_kwh=6),
        )

        self.assertGreaterEqual(simulated["battery_soc_kwh"].min(), 2)

    def test_no_simultaneous_import_export_arbitrage(self) -> None:
        simulated, _ = optimise_battery_dispatch(
            measured_for_export(),
            scenario(),
            import_rates([1, 1], rows=2),
            export_rates([50, 50], rows=2),
            BatteryAssumptions(
                allow_battery_export=True, allow_simultaneous_import_export_arbitrage=False
            ),
        )

        simultaneous = (simulated["simulated_grid_import_kwh"] > 0) & (
            simulated["simulated_grid_export_kwh"] > 0
        )
        self.assertFalse(simultaneous.any())

    def test_actual_flow_replay_versus_optimised_simulation(self) -> None:
        replay = actual_flow_replay(
            measured_for_battery(),
            scenario(),
            import_rates([5, 50, 50, 5], rows=4),
            export_rates([5, 5, 5, 5], rows=4),
        )
        _, optimised = optimise_battery_dispatch(
            measured_for_battery(),
            scenario(),
            import_rates([5, 50, 50, 5], rows=4),
            export_rates([5, 5, 5, 5], rows=4),
        )

        self.assertNotEqual(replay.net_electricity_cost_gbp, optimised.net_electricity_cost_gbp)

    def test_incomplete_historical_coverage(self) -> None:
        result = actual_flow_replay(measured(), scenario(), import_rates([20]), export_rates([5]))

        self.assertLess(result.tariff_coverage_percentage, 100)

    def test_dst_transition_timestamps_are_deterministic(self) -> None:
        replay = actual_flow_replay(
            dst_measured(),
            scenario(),
            import_rates([20, 20, 20, 20], rows=4, start="2026-10-25T00:00:00Z"),
            export_rates([5, 5, 5, 5], rows=4, start="2026-10-25T00:00:00Z"),
        )

        self.assertEqual(replay.tariff_coverage_percentage, 100)

    def test_deterministic_optimisation(self) -> None:
        first, result_a = optimise_battery_dispatch(
            measured_for_battery(),
            scenario(),
            import_rates([5, 50, 50, 5], rows=4),
            export_rates([5, 5, 5, 5], rows=4),
        )
        second, result_b = optimise_battery_dispatch(
            measured_for_battery(),
            scenario(),
            import_rates([5, 50, 50, 5], rows=4),
            export_rates([5, 5, 5, 5], rows=4),
        )

        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(result_a, result_b)

    def test_public_schema_allow_list(self) -> None:
        summary = public_summary_fixture()

        validate_public_summary(summary)
        summary["private_interval_rows"] = []
        with self.assertRaises(ValueError):
            validate_public_summary(summary)

    def test_private_interval_data_never_enters_public_output(self) -> None:
        summary = public_summary_fixture()
        summary["representative_12_month"]["scenarios"][0]["display_name"] = "contains MPAN"

        with self.assertRaises(ValueError):
            validate_public_summary(summary)

    def test_ranking_only_comparable_scenarios(self) -> None:
        measured_frame = measured(2)
        flux = actual_flow_replay(
            measured_frame,
            named_scenario("octopus_flux"),
            import_rates([20, 20]),
            export_rates([5, 5]),
        )
        alt = actual_flow_replay(
            measured_frame,
            named_scenario("agile_import_agile_outgoing", "compatibility_unconfirmed"),
            import_rates([10, 10]),
            export_rates([5, 5]),
            flux.net_electricity_cost_gbp,
        )
        simulated, optimised = optimise_battery_dispatch(
            measured_frame,
            named_scenario("octopus_flux"),
            import_rates([20, 20]),
            export_rates([5, 5]),
        )

        summary = build_public_summary(
            section_fixture(measured_frame, [flux, alt], [optimised], {"octopus_flux": simulated}),
            section_fixture(measured_frame, [flux, alt], [optimised], {"octopus_flux": simulated}),
        )

        ranks = {
            row["scenario_id"]: row["rank"]
            for row in summary["representative_12_month"]["scenarios"]
        }
        self.assertEqual(ranks["octopus_flux"], 1)
        self.assertIsNone(ranks["agile_import_agile_outgoing"])

    def test_current_tariff_repricing_basis_is_explicit(self) -> None:
        summary = public_summary_fixture()

        self.assertEqual(
            summary["representative_12_month"]["scenarios"][0]["comparison_basis"],
            "current_tariff_repriced_history",
        )

    def test_measured_replay_and_optimised_results_are_not_mixed(self) -> None:
        summary = public_summary_fixture()

        self.assertEqual(summary["representative_12_month"]["methodology"], "actual_flow_replay")
        self.assertEqual(
            summary["representative_12_month"]["experimental_optimised_scenarios"][0][
                "comparison_basis"
            ],
            "experimental_optimised_battery_simulation",
        )

    def test_annualisation_only_where_coverage_is_adequate(self) -> None:
        result = actual_flow_replay(
            measured(),
            scenario(),
            import_rates([20]),
            export_rates([5]),
            flux_baseline_cost_gbp=1.0,
        )

        self.assertLess(result.tariff_coverage_percentage, 100)
        self.assertIsNone(result.annualised_difference_vs_flux_gbp)

    def test_future_ev_scenario_is_not_ranked_by_default(self) -> None:
        active_names = {item.name for item in default_scenarios(datetime(2026, 7, 15, tzinfo=UTC))}
        future = future_ev_scenario(datetime(2026, 7, 15, tzinfo=UTC))

        self.assertNotIn(future.name, active_names)
        self.assertEqual(future.eligibility_status, "future_unmodelled")
        self.assertTrue(any("private EV charging" in note for note in future.notes))

    def test_short_case_study_is_not_annualised(self) -> None:
        summary = public_summary_fixture()

        annualised = summary["case_study"]["scenarios"][0]["annualised_difference_vs_flux_gbp"]

        self.assertIsNone(annualised)

    def test_incomplete_tariff_coverage_is_not_compared_to_flux(self) -> None:
        measured_frame = measured(2)
        flux = actual_flow_replay(
            measured_frame,
            named_scenario("octopus_flux"),
            import_rates([20, 20]),
            export_rates([5, 5]),
        )
        partial = actual_flow_replay(
            measured_frame,
            named_scenario("standard_import_prime_outgoing", "compatibility_unconfirmed"),
            import_rates([20]),
            export_rates([5]),
            flux.net_electricity_cost_gbp,
        )
        summary = build_public_summary(
            section_fixture(measured_frame, [flux, partial], [], {}),
            section_fixture(measured_frame, [flux, partial], [], {}),
        )

        rows = {row["scenario_id"]: row for row in summary["representative_12_month"]["scenarios"]}
        self.assertLess(rows["standard_import_prime_outgoing"]["coverage_percentage"], 99)
        self.assertIsNone(rows["standard_import_prime_outgoing"]["difference_vs_flux_gbp"])

    def test_representative_period_uses_latest_complete_12_months(self) -> None:
        frame = pd.DataFrame(
            {
                "settlement_start_utc": pd.to_datetime(
                    ["2025-07-01T00:00:00Z", "2026-07-12T16:30:00Z"], utc=True
                )
            }
        )

        start, end = representative_12_month_period(frame)

        self.assertEqual(start, datetime(2025, 7, 1, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 7, 1, tzinfo=UTC))

    def test_monthly_rows_show_seasonal_differences(self) -> None:
        frame = measured(4)
        frame["settlement_start_utc"] = pd.to_datetime(
            [
                "2026-01-31T23:00:00Z",
                "2026-01-31T23:30:00Z",
                "2026-02-01T00:00:00Z",
                "2026-02-01T00:30:00Z",
            ],
            utc=True,
        )
        frame["settlement_end_utc"] = frame["settlement_start_utc"] + pd.Timedelta(minutes=30)
        inputs = {
            "octopus_flux": (
                named_scenario("octopus_flux"),
                import_rates([20] * 4),
                export_rates([5] * 4),
            ),
            "agile_import_agile_outgoing": (
                named_scenario("agile_import_agile_outgoing", "compatibility_unconfirmed"),
                import_rates([10] * 4),
                export_rates([5] * 4),
            ),
        }

        rows = monthly_replay_rows(
            frame,
            inputs,
            datetime(2026, 1, 31, 23, tzinfo=UTC),
            datetime(2026, 2, 1, 1, tzinfo=UTC),
        )

        self.assertEqual({row["month"] for row in rows}, {"2026-01", "2026-02"})
        self.assertTrue(
            all(row["comparison_basis"] == "current_tariff_repriced_history" for row in rows)
        )


def scenario(
    standing_charge_p_per_day: float = 0, eligibility_status: str = "eligible"
) -> TariffScenario:
    return TariffScenario(
        name="test",
        import_product_code="IMPORT",
        export_product_code="EXPORT",
        eligibility_status=eligibility_status,
        eligibility_evidence="synthetic",
        retrieval_date=datetime(2026, 7, 15, tzinfo=UTC),
        standing_charge_p_per_day=standing_charge_p_per_day,
    )


def named_scenario(name: str, eligibility_status: str = "eligible") -> TariffScenario:
    return scenario(eligibility_status=eligibility_status).model_copy(update={"name": name})


def public_summary_fixture() -> dict[str, object]:
    measured_frame = measured()
    flux = actual_flow_replay(
        measured_frame,
        named_scenario("octopus_flux"),
        import_rates([20, 20]),
        export_rates([5, 5]),
    )
    simulated, optimised = optimise_battery_dispatch(
        measured_frame,
        named_scenario("octopus_flux"),
        import_rates([20, 20]),
        export_rates([5, 5]),
    )
    case_study = section_fixture(measured_frame, [flux], [optimised], {"octopus_flux": simulated})
    representative = section_fixture(
        measured_frame,
        [
            flux.model_copy(update={"annualised_difference_vs_flux_gbp": 0}),
        ],
        [optimised],
        {"octopus_flux": simulated},
        period_start=datetime(2025, 7, 1, tzinfo=UTC),
        period_end=datetime(2026, 7, 1, tzinfo=UTC),
    )
    return build_public_summary(case_study, representative)


def section_fixture(
    measured_frame: pd.DataFrame,
    replay_results: list,
    optimised_results: list,
    simulated_frames: dict[str, pd.DataFrame],
    period_start: datetime = datetime(2026, 7, 1, tzinfo=UTC),
    period_end: datetime = datetime(2026, 7, 2, tzinfo=UTC),
) -> dict[str, object]:
    return {
        "period_start": period_start,
        "period_end": period_end,
        "measured": measured_frame,
        "replay_results": replay_results,
        "optimised_results": optimised_results,
        "simulated_frames": simulated_frames,
        "monthly_rows": [],
    }


def measured(rows: int = 2) -> pd.DataFrame:
    start = pd.Timestamp("2026-07-14T00:00:00Z")
    return pd.DataFrame(
        {
            "settlement_start_utc": [start + pd.Timedelta(minutes=30 * i) for i in range(rows)],
            "settlement_end_utc": [start + pd.Timedelta(minutes=30 * (i + 1)) for i in range(rows)],
            "grid_import_kwh": [1.0] * rows,
            "grid_export_kwh": [1.0] * rows,
            "generation_kwh": [2.0] * rows,
            "household_demand_kwh": [2.0] * rows,
        }
    )


def measured_for_battery() -> pd.DataFrame:
    frame = measured(4)
    frame["grid_export_kwh"] = [0, 0, 0, 0]
    frame["generation_kwh"] = [0, 0, 0, 0]
    frame["household_demand_kwh"] = [0, 2, 2, 0]
    return frame


def measured_for_export() -> pd.DataFrame:
    frame = measured(2)
    frame["grid_import_kwh"] = [0, 0]
    frame["grid_export_kwh"] = [2, 2]
    frame["generation_kwh"] = [3, 3]
    frame["household_demand_kwh"] = [1, 1]
    return frame


def dst_measured() -> pd.DataFrame:
    frame = measured(2)
    start = pd.Timestamp("2026-10-25T00:00:00Z")
    frame["settlement_start_utc"] = [start, start + pd.Timedelta(hours=1)]
    frame["settlement_end_utc"] = [
        start + pd.Timedelta(minutes=30),
        start + pd.Timedelta(hours=1, minutes=30),
    ]
    return frame


def import_rates(
    values: list[float], rows: int | None = None, start: str = "2026-07-14T00:00:00Z"
) -> pd.DataFrame:
    count = rows or len(values)
    origin = pd.Timestamp(start)
    return pd.DataFrame(
        {
            "valid_from": [origin + pd.Timedelta(minutes=30 * i) for i in range(count)],
            "valid_to": [origin + pd.Timedelta(minutes=30 * (i + 1)) for i in range(count)],
            "value_inc_vat": values[:count],
        }
    )


def export_rates(
    values: list[float], rows: int | None = None, start: str = "2026-07-14T00:00:00Z"
) -> pd.DataFrame:
    return import_rates(values, rows, start)


def overlapping_rates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "valid_from": [
                pd.Timestamp("2026-07-14T00:00:00Z"),
                pd.Timestamp("2026-07-14T00:15:00Z"),
            ],
            "valid_to": [
                pd.Timestamp("2026-07-14T00:30:00Z"),
                pd.Timestamp("2026-07-14T00:45:00Z"),
            ],
            "value_inc_vat": [10, 20],
        }
    )


if __name__ == "__main__":
    unittest.main()
