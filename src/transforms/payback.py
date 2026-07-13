"""Discounted payback and NPV projection over confirmed financial benefits."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from models.payback import CapitalEvent, PaybackSummary

DEFAULT_FINANCIAL_DIR = Path("data/processed/financial")
DEFAULT_INSTALLATION_COST = 13_000.0
DEFAULT_ANNUAL_DISCOUNT_RATE = 0.05
DEFAULT_INSTALLATION_DATE = date(2023, 1, 24)
DEFAULT_PROJECTION_YEARS = 25


def run_payback_pipeline(
    financial_dir: Path = DEFAULT_FINANCIAL_DIR,
    installation_cost: float = DEFAULT_INSTALLATION_COST,
    annual_discount_rate: float = DEFAULT_ANNUAL_DISCOUNT_RATE,
    installation_date: date = DEFAULT_INSTALLATION_DATE,
    capital_events: list[CapitalEvent] | None = None,
) -> PaybackSummary:
    financial_dir.mkdir(parents=True, exist_ok=True)
    historical = read_historical_monthly_benefits(financial_dir / "monthly_financial_summary.csv")
    projected = build_projected_cash_flows(
        historical,
        installation_cost=installation_cost,
        annual_discount_rate=annual_discount_rate,
        installation_date=installation_date,
        capital_events=capital_events or [],
    )
    summary = build_payback_summary(
        projected,
        installation_cost=installation_cost,
        annual_discount_rate=annual_discount_rate,
        installation_date=installation_date,
    )

    write_parquet(projected, financial_dir / "payback_projected_cash_flows.parquet")
    projected.to_csv(financial_dir / "payback_projected_cash_flows.csv", index=False)
    build_annual_summary(projected).to_csv(
        financial_dir / "payback_annual_summary.csv", index=False
    )
    (financial_dir / "payback_public_summary.json").write_text(
        json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def read_historical_monthly_benefits(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"period", "avoided_import_value", "export_income", "total_financial_benefit"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Monthly financial summary missing columns: {sorted(missing)}")
    frame = frame.copy()
    frame["period"] = pd.PeriodIndex(frame["period"], freq="M")
    frame["cash_flow_date"] = frame["period"].dt.end_time.dt.date
    frame["avoided_import_value"] = pd.to_numeric(frame["avoided_import_value"], errors="coerce")
    frame["export_income"] = pd.to_numeric(frame["export_income"], errors="coerce")
    frame["total_financial_benefit"] = pd.to_numeric(
        frame["total_financial_benefit"], errors="coerce"
    )
    return frame.sort_values("period").reset_index(drop=True)


def build_projected_cash_flows(
    historical: pd.DataFrame,
    installation_cost: float,
    annual_discount_rate: float,
    installation_date: date,
    capital_events: list[CapitalEvent],
    projection_years: int = DEFAULT_PROJECTION_YEARS,
) -> pd.DataFrame:
    monthly_rate = effective_monthly_rate(annual_discount_rate)
    projection_end = add_years(installation_date, projection_years)
    rows: list[dict[str, object]] = []

    for row in historical.itertuples(index=False):
        rows.append(
            cash_flow_row(
                period=row.period,
                cash_flow_date=row.cash_flow_date,
                avoided_import_value=float(row.avoided_import_value),
                export_income=float(row.export_income),
                total_financial_benefit=float(row.total_financial_benefit),
                flow_type="historical_confirmed_benefit",
                source_status="confirmed",
                installation_date=installation_date,
                monthly_rate=monthly_rate,
            )
        )

    for event in capital_events:
        event_period = pd.Period(event.event_date, freq="M")
        rows.append(
            cash_flow_row(
                period=event_period,
                cash_flow_date=event.event_date,
                avoided_import_value=0.0,
                export_income=0.0,
                total_financial_benefit=-abs(event.amount_gbp),
                flow_type=f"capital_event:{event.category}",
                source_status="capital_event",
                installation_date=installation_date,
                monthly_rate=monthly_rate,
                description=event.description,
            )
        )

    seasonal = seasonal_monthly_profile(historical)
    if historical.empty:
        raise ValueError("At least one historical month is required for projection.")
    next_period = historical["period"].max() + 1
    while next_period.end_time.date() <= projection_end:
        benefit = float(seasonal.loc[next_period.month, "total_financial_benefit"])
        rows.append(
            cash_flow_row(
                period=next_period,
                cash_flow_date=next_period.end_time.date(),
                avoided_import_value=float(seasonal.loc[next_period.month, "avoided_import_value"]),
                export_income=float(seasonal.loc[next_period.month, "export_income"]),
                total_financial_benefit=benefit,
                flow_type="projected_seasonal_benefit",
                source_status="projected_from_measured_history",
                installation_date=installation_date,
                monthly_rate=monthly_rate,
            )
        )
        frame = cumulative_frame(rows, installation_cost)
        if frame["discounted_payback_reached"].any():
            break
        next_period += 1

    return cumulative_frame(rows, installation_cost)


def cash_flow_row(
    period: pd.Period,
    cash_flow_date: date,
    avoided_import_value: float,
    export_income: float,
    total_financial_benefit: float,
    flow_type: str,
    source_status: str,
    installation_date: date,
    monthly_rate: float,
    description: str = "",
) -> dict[str, object]:
    months = months_between(installation_date, cash_flow_date)
    discount_factor = (1 + monthly_rate) ** months
    discounted = total_financial_benefit / discount_factor
    return {
        "period": str(period),
        "calendar_month": period.month,
        "cash_flow_date": cash_flow_date.isoformat(),
        "flow_type": flow_type,
        "source_status": source_status,
        "description": description,
        "avoided_import_value": round(avoided_import_value, 6),
        "export_income": round(export_income, 6),
        "total_financial_benefit": round(total_financial_benefit, 6),
        "months_from_installation": months,
        "discount_factor": round(discount_factor, 12),
        "discounted_cash_flow": round(discounted, 6),
    }


def cumulative_frame(rows: list[dict[str, object]], installation_cost: float) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame = frame.sort_values(["cash_flow_date", "flow_type"]).reset_index(drop=True)
    frame["cumulative_nominal_benefit"] = frame["total_financial_benefit"].cumsum()
    frame["cumulative_discounted_benefit"] = frame["discounted_cash_flow"].cumsum()
    frame["nominal_recovery_percentage"] = (
        frame["cumulative_nominal_benefit"] / installation_cost * 100
    )
    frame["discounted_recovery_percentage"] = (
        frame["cumulative_discounted_benefit"] / installation_cost * 100
    )
    frame["npv"] = frame["cumulative_discounted_benefit"] - installation_cost
    frame["simple_payback_reached"] = frame["cumulative_nominal_benefit"] >= installation_cost
    frame["discounted_payback_reached"] = (
        frame["cumulative_discounted_benefit"] >= installation_cost
    )
    return frame


def seasonal_monthly_profile(historical: pd.DataFrame) -> pd.DataFrame:
    frame = historical.copy()
    frame["calendar_month"] = frame["period"].dt.month
    profile = (
        frame.groupby("calendar_month", dropna=False)
        .agg(
            avoided_import_value=("avoided_import_value", "mean"),
            export_income=("export_income", "mean"),
            total_financial_benefit=("total_financial_benefit", "mean"),
            observed_years=("period", "count"),
        )
        .reindex(range(1, 13))
    )
    if profile["total_financial_benefit"].isna().any():
        fallback = float(frame["total_financial_benefit"].mean())
        profile["total_financial_benefit"] = profile["total_financial_benefit"].fillna(fallback)
        profile["avoided_import_value"] = profile["avoided_import_value"].fillna(0.0)
        profile["export_income"] = profile["export_income"].fillna(
            profile["total_financial_benefit"]
        )
        profile["observed_years"] = profile["observed_years"].fillna(0)
    return profile


def build_payback_summary(
    cash_flows: pd.DataFrame,
    installation_cost: float,
    annual_discount_rate: float,
    installation_date: date,
) -> PaybackSummary:
    historical = cash_flows[cash_flows["source_status"] == "confirmed"]
    historical_nominal = float(historical["total_financial_benefit"].sum())
    historical_discounted = float(historical["discounted_cash_flow"].sum())
    current_npv = historical_discounted - installation_cost
    return PaybackSummary(
        installation_cost=installation_cost,
        annual_discount_rate=annual_discount_rate,
        effective_monthly_discount_rate=effective_monthly_rate(annual_discount_rate),
        installation_date=installation_date,
        confirmed_lifetime_nominal_benefit=round(historical_nominal, 2),
        discounted_historical_benefit=round(historical_discounted, 2),
        nominal_recovery_percentage=round(historical_nominal / installation_cost * 100, 4),
        discounted_recovery_percentage=round(historical_discounted / installation_cost * 100, 4),
        current_npv=round(current_npv, 2),
        projected_simple_payback_month=first_payback_period(cash_flows, "simple_payback_reached"),
        projected_discounted_payback_month=first_payback_period(
            cash_flows, "discounted_payback_reached"
        ),
        projection_end_date=pd.to_datetime(cash_flows["cash_flow_date"]).dt.date.max(),
        calculation_status="projected_from_measured_history",
        modelling_assumptions=[
            "Installation cost is 13000 GBP.",
            "Annual discount/opportunity-cost rate is 5%, converted to an effective monthly rate.",
            "Historical benefits use confirmed SolaX and Octopus monthly financial results.",
            "Future monthly benefits use the measured calendar-month seasonal profile.",
            "Tariffs and system performance are held constant in real terms.",
            "No panel or battery degradation is assumed.",
            "No electricity-price inflation is assumed.",
            "No personal loans, mortgage rates, deposits or repayment history are modelled.",
            "Future replacements or upgrades should be added later as separate dated capital cash-flow events.",
        ],
        generated_at=datetime.now(UTC),
    )


def build_annual_summary(cash_flows: pd.DataFrame) -> pd.DataFrame:
    frame = cash_flows.copy()
    frame["year"] = pd.to_datetime(frame["cash_flow_date"]).dt.year
    return (
        frame.groupby("year", dropna=False)
        .agg(
            nominal_cash_flow=("total_financial_benefit", "sum"),
            discounted_cash_flow=("discounted_cash_flow", "sum"),
            closing_cumulative_nominal_benefit=("cumulative_nominal_benefit", "last"),
            closing_cumulative_discounted_benefit=("cumulative_discounted_benefit", "last"),
            closing_npv=("npv", "last"),
        )
        .reset_index()
    )


def first_payback_period(cash_flows: pd.DataFrame, column: str) -> str | None:
    reached = cash_flows[cash_flows[column]]
    if reached.empty:
        return None
    return str(reached.iloc[0]["period"])


def effective_monthly_rate(annual_rate: float) -> float:
    return (1 + annual_rate) ** (1 / 12) - 1


def months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


def write_parquet(frame: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, path, compression="snappy")


def main() -> int:
    summary = run_payback_pipeline()
    print(summary.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
