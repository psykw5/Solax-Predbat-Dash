from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from models.payback import CapitalEvent
from transforms.payback import (
    build_payback_summary,
    build_projected_cash_flows,
    effective_monthly_rate,
    months_between,
    seasonal_monthly_profile,
)


def historical_months(values: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period": pd.PeriodIndex([period for period, _ in values], freq="M"),
            "cash_flow_date": [pd.Period(period, freq="M").end_time.date() for period, _ in values],
            "avoided_import_value": [value * 0.6 for _, value in values],
            "export_income": [value * 0.4 for _, value in values],
            "total_financial_benefit": [value for _, value in values],
        }
    )


class PaybackProjectionTests(unittest.TestCase):
    def test_monthly_discounting_uses_effective_monthly_rate(self) -> None:
        historical = historical_months([("2023-02", 120.0)])
        flows = build_projected_cash_flows(
            historical,
            installation_cost=10_000,
            annual_discount_rate=0.05,
            installation_date=date(2023, 1, 1),
            capital_events=[],
        )

        first = flows.iloc[0]
        expected = 120 / (1 + effective_monthly_rate(0.05))
        self.assertEqual(first["months_from_installation"], 1)
        self.assertAlmostEqual(first["discounted_cash_flow"], expected, places=6)

    def test_seasonal_projection_uses_calendar_month_profile(self) -> None:
        historical = historical_months(
            [
                ("2023-01", 100.0),
                ("2024-01", 300.0),
                ("2023-02", 50.0),
                ("2024-02", 150.0),
            ]
        )

        profile = seasonal_monthly_profile(historical)

        self.assertEqual(profile.loc[1, "total_financial_benefit"], 200.0)
        self.assertEqual(profile.loc[2, "total_financial_benefit"], 100.0)

    def test_crossing_simple_payback_threshold(self) -> None:
        historical = historical_months([("2023-01", 400.0), ("2023-02", 400.0)])
        flows = build_projected_cash_flows(
            historical,
            installation_cost=1_000,
            annual_discount_rate=0.0,
            installation_date=date(2023, 1, 1),
            capital_events=[],
        )
        summary = build_payback_summary(flows, 1_000, 0.0, date(2023, 1, 1))

        self.assertEqual(summary.projected_simple_payback_month, "2023-03")

    def test_crossing_discounted_payback_threshold(self) -> None:
        historical = historical_months([("2023-01", 500.0), ("2023-02", 500.0)])
        flows = build_projected_cash_flows(
            historical,
            installation_cost=1_200,
            annual_discount_rate=0.05,
            installation_date=date(2023, 1, 1),
            capital_events=[],
        )
        summary = build_payback_summary(flows, 1_200, 0.05, date(2023, 1, 1))

        self.assertIsNotNone(summary.projected_discounted_payback_month)

    def test_no_payback_within_25_years(self) -> None:
        historical = historical_months([("2023-01", 1.0)])
        flows = build_projected_cash_flows(
            historical,
            installation_cost=100_000,
            annual_discount_rate=0.05,
            installation_date=date(2023, 1, 1),
            capital_events=[],
        )
        summary = build_payback_summary(flows, 100_000, 0.05, date(2023, 1, 1))

        self.assertIsNone(summary.projected_simple_payback_month)
        self.assertIsNone(summary.projected_discounted_payback_month)

    def test_future_capital_event_shifts_payback_date(self) -> None:
        historical = historical_months([("2023-01", 400.0), ("2023-02", 400.0)])
        without_event = build_projected_cash_flows(
            historical,
            installation_cost=1_000,
            annual_discount_rate=0.0,
            installation_date=date(2023, 1, 1),
            capital_events=[],
        )
        with_event = build_projected_cash_flows(
            historical,
            installation_cost=1_000,
            annual_discount_rate=0.0,
            installation_date=date(2023, 1, 1),
            capital_events=[
                CapitalEvent(
                    event_date=date(2023, 3, 15),
                    amount_gbp=500.0,
                    category="battery_upgrade",
                )
            ],
        )

        baseline = build_payback_summary(without_event, 1_000, 0.0, date(2023, 1, 1))
        shifted = build_payback_summary(with_event, 1_000, 0.0, date(2023, 1, 1))
        self.assertGreater(
            shifted.projected_simple_payback_month, baseline.projected_simple_payback_month
        )

    def test_leap_year_and_partial_month_boundaries(self) -> None:
        self.assertEqual(months_between(date(2024, 2, 29), date(2024, 3, 28)), 0)
        self.assertEqual(months_between(date(2024, 2, 29), date(2024, 3, 29)), 1)


if __name__ == "__main__":
    unittest.main()
