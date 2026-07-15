"""Real private tariff comparison runner and public-safe summary builder."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tariffs.models import BatteryAssumptions, ScenarioResult, TariffScenario
from tariffs.octopus_discovery import (
    PublicOctopusClient,
    standing_charges_url,
    tariff_code,
    unit_rates_url,
    write_json,
)
from tariffs.whatif import (
    actual_flow_replay,
    default_scenarios,
    measured_half_hours_from_solax,
    optimise_battery_dispatch,
)

DEFAULT_SOLAX_PATH = Path("data/processed/solax/solax_intervals.parquet")
DEFAULT_AGREEMENTS_PATH = Path("data/processed/octopus/tariff_agreements.parquet")
DEFAULT_RAW_DIR = Path("data/raw/octopus/tariff_whatif")
DEFAULT_OUTPUT_DIR = Path("data/processed/tariff_whatif")
DEFAULT_PUBLIC_PATH = Path("data/public/wattson-tariff-comparison.json")

SCENARIO_PRODUCTS = {
    "octopus_flux": ("FLUX-IMPORT-23-02-14", "FLUX-EXPORT-23-02-14"),
    "agile_import_agile_outgoing": ("AGILE-24-10-01", "AGILE-OUTGOING-19-05-13"),
    "agile_import_flat_outgoing": ("AGILE-24-10-01", "OUTGOING-VAR-24-10-26"),
    "standard_import_prime_outgoing": ("VAR-22-11-01", "OUTGOING-PRIME-FIX-12M-26-06-23"),
}

DISPLAY_NAMES = {
    "octopus_flux": "Octopus Flux",
    "agile_import_agile_outgoing": "Agile import + Agile Outgoing",
    "agile_import_flat_outgoing": "Agile import + flat Outgoing",
    "standard_import_prime_outgoing": "Flexible import + Prime Outgoing",
}


def run_real_tariff_comparison(
    solax_path: Path = DEFAULT_SOLAX_PATH,
    agreements_path: Path = DEFAULT_AGREEMENTS_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    public_path: Path = DEFAULT_PUBLIC_PATH,
    client: PublicOctopusClient | None = None,
) -> dict[str, Any]:
    octopus = client or PublicOctopusClient()
    region = infer_tariff_region(agreements_path)
    measured_all = measured_half_hours_from_solax(pd.read_parquet(solax_path))
    measured_all["settlement_start_utc"] = pd.to_datetime(
        measured_all["settlement_start_utc"], utc=True
    )
    measured_all["settlement_end_utc"] = pd.to_datetime(
        measured_all["settlement_end_utc"], utc=True
    )
    data_end = measured_all["settlement_end_utc"].max().to_pydatetime()

    product_payloads = {
        product: octopus.product(product)
        for products in SCENARIO_PRODUCTS.values()
        for product in products
    }
    for product, payload in product_payloads.items():
        write_json(raw_dir / "products" / f"{product}.json", redact_product_payload(payload))

    case_study_start = max(
        parse_datetime(product_payloads[product]["available_from"])
        for products in SCENARIO_PRODUCTS.values()
        for product in products
    )
    representative_start, representative_end = representative_12_month_period(measured_all)
    case_study_end = data_end + timedelta(minutes=30)
    retrieval = datetime.now(UTC)
    battery = BatteryAssumptions()
    case_study = run_period_comparison(
        octopus,
        region,
        measured_all,
        case_study_start,
        case_study_end,
        raw_dir,
        battery,
        retrieval,
    )
    representative = run_period_comparison(
        octopus,
        region,
        measured_all,
        representative_start,
        representative_end,
        raw_dir,
        battery,
        retrieval,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    for section_name, section in {
        "case_study": case_study,
        "representative_12_month": representative,
    }.items():
        for result in section["replay_results"] + section["optimised_results"]:
            row = result.model_dump()
            row["section"] = section_name
            summary_rows.append(row)
        for name, frame in section["simulated_frames"].items():
            frame.to_parquet(
                output_dir / f"{section_name}_{name}_real_optimised_half_hourly.parquet",
                index=False,
            )
    pd.DataFrame(summary_rows).to_csv(output_dir / "real_scenario_summary.csv", index=False)
    pd.DataFrame(representative["monthly_rows"]).to_csv(
        output_dir / "representative_monthly_scenario_summary.csv", index=False
    )

    public_summary = build_public_summary(
        case_study,
        representative,
    )
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(json.dumps(public_summary, indent=2, sort_keys=True), encoding="utf-8")
    write_json(
        output_dir / "real_comparison_audit.json",
        {
            "comparison_basis": "current_tariff_repriced_history",
            "retrieval_date": retrieval.isoformat(),
            "region_code": region,
            "rate_coverage": {
                "case_study": case_study["rate_coverage"],
                "representative_12_month": representative["rate_coverage"],
            },
            "public_summary": public_summary,
        },
    )
    return {
        "public_summary": public_summary,
        "case_study": case_study,
        "representative_12_month": representative,
    }


def build_public_summary(
    case_study: dict[str, Any],
    representative: dict[str, Any],
) -> dict[str, Any]:
    summary = {
        "baseline_scenario": "octopus_flux",
        "primary_comparison": "representative_12_month",
        "case_study": section_summary(case_study, allow_annualised=False),
        "representative_12_month": section_summary(representative, allow_annualised=True),
        "notes": [
            "Measured-flow replay prices the observed household flows without changing battery behaviour.",
            "Experimental optimisation is separate and does not represent actual inverter control.",
            "Compatibility-unconfirmed scenarios are shown for context but are not ranked.",
        ],
    }
    validate_public_summary(summary)
    return summary


def section_summary(section: dict[str, Any], allow_annualised: bool) -> dict[str, Any]:
    measured = section["measured"]
    replay_results = section["replay_results"]
    optimised_results = section["optimised_results"]
    simulated_frames = section["simulated_frames"]
    period_start = section["period_start"]
    period_end = section["period_end"]
    duration_days = max((period_end - period_start).days, 1)
    scenario_rows = []
    comparable = [
        result
        for result in replay_results
        if result.tariff_coverage_percentage >= 99 and result.eligibility_status == "eligible"
    ]
    ranks = {
        result.scenario: rank + 1
        for rank, result in enumerate(
            sorted(comparable, key=lambda item: item.net_electricity_cost_gbp)
        )
    }
    for result in replay_results:
        scenario_rows.append(
            {
                "scenario_id": result.scenario,
                "display_name": DISPLAY_NAMES[result.scenario],
                "eligibility_status": result.eligibility_status,
                "comparison_basis": "current_tariff_repriced_history",
                "coverage_percentage": result.tariff_coverage_percentage,
                "net_cost_gbp": result.net_electricity_cost_gbp,
                "difference_vs_flux_gbp": result.difference_vs_flux_gbp
                if result.tariff_coverage_percentage >= 99
                else None,
                "annualised_difference_vs_flux_gbp": result.annualised_difference_vs_flux_gbp
                if allow_annualised
                and duration_days >= 90
                and result.tariff_coverage_percentage >= 99
                else None,
                "rank": ranks.get(result.scenario),
                "data_quality_status": result.data_quality_status,
            }
        )
    experimental = []
    for result in optimised_results:
        frame = simulated_frames.get(result.scenario, pd.DataFrame())
        experimental.append(
            {
                "scenario_id": result.scenario,
                "display_name": DISPLAY_NAMES[result.scenario],
                "eligibility_status": result.eligibility_status,
                "comparison_basis": "experimental_optimised_battery_simulation",
                "coverage_percentage": result.tariff_coverage_percentage,
                "net_cost_gbp": result.net_electricity_cost_gbp,
                "difference_vs_flux_gbp": result.difference_vs_flux_gbp
                if result.tariff_coverage_percentage >= 99
                else None,
                "battery_throughput_kwh": result.battery_throughput_kwh,
                "equivalent_full_battery_cycles": result.equivalent_full_battery_cycles,
                "grid_charge_half_hours": int(
                    (frame.get("grid_charge_kwh", pd.Series(dtype=float)) > 0).sum()
                ),
                "battery_export_half_hours": 0,
                "data_quality_status": result.data_quality_status,
            }
        )
    return {
        "reporting_period": {
            "start_month": period_start.strftime("%Y-%m"),
            "end_month": month_label_for_exclusive_end(period_end),
            "half_hour_count": int(len(measured)),
            "expected_half_hours": expected_half_hours(period_start, period_end),
            "energy_coverage_percentage": energy_coverage_percent(
                measured, period_start, period_end
            ),
            "comparison_basis": "current_tariff_repriced_history",
        },
        "methodology": "actual_flow_replay",
        "scenarios": scenario_rows,
        "monthly_scenarios": section["monthly_rows"],
        "experimental_optimised_scenarios": experimental,
    }


def validate_public_summary(summary: dict[str, Any]) -> None:
    allowed_top = {
        "baseline_scenario",
        "primary_comparison",
        "case_study",
        "representative_12_month",
        "notes",
    }
    if set(summary) != allowed_top:
        raise ValueError("Public tariff summary has unexpected top-level fields.")
    allowed_scenario = {
        "scenario_id",
        "display_name",
        "eligibility_status",
        "comparison_basis",
        "coverage_percentage",
        "net_cost_gbp",
        "difference_vs_flux_gbp",
        "annualised_difference_vs_flux_gbp",
        "rank",
        "data_quality_status",
    }
    allowed_section = {
        "reporting_period",
        "methodology",
        "scenarios",
        "monthly_scenarios",
        "experimental_optimised_scenarios",
    }
    allowed_period = {
        "start_month",
        "end_month",
        "half_hour_count",
        "expected_half_hours",
        "energy_coverage_percentage",
        "comparison_basis",
    }
    allowed_month = {
        "month",
        "scenario_id",
        "display_name",
        "eligibility_status",
        "comparison_basis",
        "coverage_percentage",
        "energy_coverage_percentage",
        "net_cost_gbp",
        "difference_vs_flux_gbp",
        "data_quality_status",
    }
    for section_name in ["case_study", "representative_12_month"]:
        section = summary[section_name]
        if set(section) != allowed_section:
            raise ValueError("Public tariff section has unexpected fields.")
        if set(section["reporting_period"]) != allowed_period:
            raise ValueError("Public tariff period has unexpected fields.")
        for row in section["scenarios"]:
            if set(row) != allowed_scenario:
                raise ValueError("Public tariff scenario row has unexpected fields.")
        for row in section["monthly_scenarios"]:
            if set(row) != allowed_month:
                raise ValueError("Public tariff monthly row has unexpected fields.")
    forbidden = json.dumps(summary).lower()
    for token in [
        "mpan",
        "serial",
        "account",
        "meter",
        "filename",
        "endpoint",
        "latitude",
        "longitude",
        "zappi",
        "leaf",
    ]:
        if token in forbidden:
            raise ValueError(f"Public tariff summary contains private token: {token}")


def run_period_comparison(
    octopus: PublicOctopusClient,
    region: str,
    measured_all: pd.DataFrame,
    period_start: datetime,
    period_end: datetime,
    raw_dir: Path,
    battery: BatteryAssumptions,
    retrieval: datetime,
) -> dict[str, Any]:
    measured = measured_all[
        (measured_all["settlement_start_utc"] >= period_start)
        & (measured_all["settlement_end_utc"] < period_end)
    ].copy()
    if measured.empty:
        raise ValueError(
            "No measured half-hourly energy data overlaps the tariff comparison period."
        )

    scenario_inputs: dict[str, tuple[TariffScenario, pd.DataFrame, pd.DataFrame]] = {}
    rate_coverage: dict[str, dict[str, object]] = {}
    for scenario in default_scenarios(retrieval):
        import_product, export_product = SCENARIO_PRODUCTS[scenario.name]
        scenario = scenario.model_copy(
            update={
                "import_product_code": import_product,
                "export_product_code": export_product,
                "eligibility_status": eligibility_for(scenario.name),
                "eligibility_evidence": evidence_for(scenario.name),
                "standing_charge_p_per_day": standing_charge_for_product(
                    octopus, import_product, region, period_start, period_end, raw_dir
                ),
            }
        )
        import_rates = fetch_rates(
            octopus, import_product, region, period_start, period_end, raw_dir
        )
        export_rates = fetch_rates(
            octopus, export_product, region, period_start, period_end, raw_dir
        )
        scenario_inputs[scenario.name] = (scenario, import_rates, export_rates)
        rate_coverage[scenario.name] = {
            "import_rate_count": len(import_rates),
            "export_rate_count": len(export_rates),
            "standing_charge_p_per_day": scenario.standing_charge_p_per_day,
            "import_product": import_product,
            "export_product": export_product,
            "import_tariff_code": tariff_code(import_product, region),
            "export_tariff_code": tariff_code(export_product, region),
        }

    flux_replay = actual_flow_replay(measured, *scenario_inputs["octopus_flux"])
    flux_optimised = optimise_battery_dispatch(measured, *scenario_inputs["octopus_flux"], battery)[
        1
    ]
    replay_results: list[ScenarioResult] = []
    optimised_results: list[ScenarioResult] = []
    simulated_frames: dict[str, pd.DataFrame] = {}
    for name, (scenario, import_rates, export_rates) in scenario_inputs.items():
        replay_results.append(
            actual_flow_replay(
                measured, scenario, import_rates, export_rates, flux_replay.net_electricity_cost_gbp
            )
        )
        simulated, optimised = optimise_battery_dispatch(
            measured,
            scenario,
            import_rates,
            export_rates,
            battery,
            flux_optimised.net_electricity_cost_gbp,
        )
        optimised_results.append(optimised)
        simulated_frames[name] = simulated

    return {
        "period_start": period_start,
        "period_end": period_end,
        "measured": measured,
        "scenario_inputs": scenario_inputs,
        "rate_coverage": rate_coverage,
        "replay_results": replay_results,
        "optimised_results": optimised_results,
        "simulated_frames": simulated_frames,
        "monthly_rows": monthly_replay_rows(measured, scenario_inputs, period_start, period_end),
    }


def monthly_replay_rows(
    measured: pd.DataFrame,
    scenario_inputs: dict[str, tuple[TariffScenario, pd.DataFrame, pd.DataFrame]],
    period_start: datetime,
    period_end: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    month_start = pd.Timestamp(period_start)
    while month_start < pd.Timestamp(period_end):
        natural_month_end = pd.Timestamp(
            year=month_start.year, month=month_start.month, day=1, tz=UTC
        ) + pd.DateOffset(months=1)
        month_end = min(natural_month_end, pd.Timestamp(period_end))
        month_start_dt = month_start.to_pydatetime()
        month_end_dt = month_end.to_pydatetime()
        month_measured = measured[
            (measured["settlement_start_utc"] >= month_start_dt)
            & (measured["settlement_end_utc"] <= month_end_dt)
        ].copy()
        if not month_measured.empty:
            flux = actual_flow_replay(month_measured, *scenario_inputs["octopus_flux"])
            month_energy_coverage = energy_coverage_percent(
                month_measured, month_start_dt, month_end_dt
            )
            for scenario_id, (scenario, import_rates, export_rates) in scenario_inputs.items():
                result = actual_flow_replay(
                    month_measured,
                    scenario,
                    import_rates,
                    export_rates,
                    flux.net_electricity_cost_gbp,
                )
                rows.append(
                    {
                        "month": month_start.strftime("%Y-%m"),
                        "scenario_id": scenario_id,
                        "display_name": DISPLAY_NAMES[scenario_id],
                        "eligibility_status": result.eligibility_status,
                        "comparison_basis": "current_tariff_repriced_history",
                        "coverage_percentage": result.tariff_coverage_percentage,
                        "energy_coverage_percentage": month_energy_coverage,
                        "net_cost_gbp": result.net_electricity_cost_gbp,
                        "difference_vs_flux_gbp": result.difference_vs_flux_gbp
                        if result.tariff_coverage_percentage >= 99
                        else None,
                        "data_quality_status": result.data_quality_status,
                    }
                )
        month_start = month_end
    return rows


def representative_12_month_period(measured: pd.DataFrame) -> tuple[datetime, datetime]:
    latest_start = pd.to_datetime(measured["settlement_start_utc"], utc=True).max()
    latest_complete_month_start = pd.Timestamp(
        year=latest_start.year, month=latest_start.month, day=1, tz=UTC
    )
    period_end = latest_complete_month_start
    period_start = (period_end - pd.DateOffset(months=12)).to_pydatetime()
    return period_start, period_end.to_pydatetime()


def expected_half_hours(period_start: datetime, period_end: datetime) -> int:
    duration = pd.Timestamp(period_end) - pd.Timestamp(period_start)
    return int(duration / pd.Timedelta(minutes=30))


def energy_coverage_percent(
    measured: pd.DataFrame, period_start: datetime, period_end: datetime
) -> float:
    expected = expected_half_hours(period_start, period_end)
    return round(len(measured) / expected * 100, 4) if expected else 0.0


def month_label_for_exclusive_end(period_end: datetime) -> str:
    return (pd.Timestamp(period_end) - pd.Timedelta(minutes=30)).strftime("%Y-%m")


def fetch_rates(
    client: PublicOctopusClient,
    product_code: str,
    region: str,
    period_start: datetime,
    period_end: datetime,
    raw_dir: Path,
) -> pd.DataFrame:
    url = unit_rates_url(product_code, region, period_start, period_end)
    rows = client.paged(url)
    write_json(raw_dir / "rates" / f"{product_code}_standard_unit_rates.json", rows)
    return pd.DataFrame(
        [
            {
                "valid_from": parse_datetime(row["valid_from"]),
                "valid_to": parse_optional_datetime(row.get("valid_to")),
                "value_inc_vat": float(row["value_inc_vat"]),
            }
            for row in rows
            if row.get("valid_from") and row.get("value_inc_vat") is not None
        ]
    )


def standing_charge_for_product(
    client: PublicOctopusClient,
    product_code: str,
    region: str,
    period_start: datetime,
    period_end: datetime,
    raw_dir: Path,
) -> float:
    url = standing_charges_url(product_code, region, period_start, period_end)
    rows = client.paged(url)
    write_json(raw_dir / "standing_charges" / f"{product_code}_standing_charges.json", rows)
    if not rows:
        detail = client.product(product_code)
        tariff = (
            detail.get("single_register_electricity_tariffs", {})
            .get(f"_{region.upper()}", {})
            .get("direct_debit_monthly", {})
        )
        return float(tariff.get("standing_charge_inc_vat", 0.0))
    latest = sorted(rows, key=lambda row: row.get("valid_from") or "")[-1]
    return float(latest["value_inc_vat"])


def infer_tariff_region(agreements_path: Path) -> str:
    agreements = pd.read_parquet(agreements_path)
    codes = agreements["tariff_code"].dropna().astype(str)
    for code in codes:
        parts = code.split("-")
        if parts:
            return parts[-1].upper()
    raise ValueError("Unable to infer Octopus tariff region from processed agreements.")


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_optional_datetime(value: str | None) -> datetime | None:
    return None if value is None else parse_datetime(value)


def eligibility_for(scenario_id: str) -> str:
    if scenario_id == "octopus_flux":
        return "eligible"
    return "compatibility_unconfirmed"


def evidence_for(scenario_id: str) -> str:
    evidence = {
        "octopus_flux": "Official Octopus Flux documentation describes a combined import/export tariff for solar and battery owners with smart meter and export requirements.",
        "agile_import_agile_outgoing": "Official product API confirms both products exist; current import/export pairing permission is not explicitly confirmed.",
        "agile_import_flat_outgoing": "Official product API confirms both products exist; current import/export pairing permission is not explicitly confirmed.",
        "standard_import_prime_outgoing": "Official product API confirms Flexible and Prime Outgoing products exist; Prime export eligibility and pairing remain compatibility-unconfirmed.",
    }
    return evidence[scenario_id]


def redact_product_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": payload.get("code"),
        "display_name": payload.get("display_name"),
        "full_name": payload.get("full_name"),
        "description": payload.get("description"),
        "available_from": payload.get("available_from"),
        "available_to": payload.get("available_to"),
        "is_variable": payload.get("is_variable"),
        "is_restricted": payload.get("is_restricted"),
        "is_green": payload.get("is_green"),
    }
