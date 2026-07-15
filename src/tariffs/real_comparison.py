"""Real private tariff comparison runner and public-safe summary builder."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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

    period_start = max(
        parse_datetime(product_payloads[product]["available_from"])
        for products in SCENARIO_PRODUCTS.values()
        for product in products
    )
    period_end = data_end
    measured = measured_all[
        (measured_all["settlement_start_utc"] >= period_start)
        & (measured_all["settlement_end_utc"] <= period_end)
    ].copy()
    if measured.empty:
        raise ValueError(
            "No measured half-hourly energy data overlaps the comparable tariff period."
        )

    retrieval = datetime.now(UTC)
    battery = BatteryAssumptions()
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

    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([result.model_dump() for result in replay_results + optimised_results]).to_csv(
        output_dir / "real_scenario_summary.csv", index=False
    )
    for name, frame in simulated_frames.items():
        frame.to_parquet(output_dir / f"{name}_real_optimised_half_hourly.parquet", index=False)

    public_summary = build_public_summary(
        measured,
        replay_results,
        optimised_results,
        simulated_frames,
        period_start,
        period_end,
    )
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(json.dumps(public_summary, indent=2, sort_keys=True), encoding="utf-8")
    write_json(
        output_dir / "real_comparison_audit.json",
        {
            "comparison_basis": "current_tariff_repriced_history",
            "retrieval_date": retrieval.isoformat(),
            "region_code": region,
            "rate_coverage": rate_coverage,
            "public_summary": public_summary,
        },
    )
    return {
        "public_summary": public_summary,
        "replay_results": replay_results,
        "optimised_results": optimised_results,
        "rate_coverage": rate_coverage,
    }


def build_public_summary(
    measured: pd.DataFrame,
    replay_results: list[ScenarioResult],
    optimised_results: list[ScenarioResult],
    simulated_frames: dict[str, pd.DataFrame],
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
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
                "difference_vs_flux_gbp": result.difference_vs_flux_gbp,
                "annualised_difference_vs_flux_gbp": result.annualised_difference_vs_flux_gbp,
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
                "difference_vs_flux_gbp": result.difference_vs_flux_gbp,
                "battery_throughput_kwh": result.battery_throughput_kwh,
                "equivalent_full_battery_cycles": result.equivalent_full_battery_cycles,
                "grid_charge_half_hours": int(
                    (frame.get("grid_charge_kwh", pd.Series(dtype=float)) > 0).sum()
                ),
                "battery_export_half_hours": 0,
                "data_quality_status": result.data_quality_status,
            }
        )
    summary = {
        "reporting_period": {
            "start_month": period_start.strftime("%Y-%m"),
            "end_month": period_end.strftime("%Y-%m"),
            "half_hour_count": int(len(measured)),
        },
        "baseline_scenario": "octopus_flux",
        "methodology": "actual_flow_replay",
        "scenarios": scenario_rows,
        "experimental_optimised_scenarios": experimental,
    }
    validate_public_summary(summary)
    return summary


def validate_public_summary(summary: dict[str, Any]) -> None:
    allowed_top = {
        "reporting_period",
        "baseline_scenario",
        "methodology",
        "scenarios",
        "experimental_optimised_scenarios",
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
    for row in summary["scenarios"]:
        if set(row) != allowed_scenario:
            raise ValueError("Public tariff scenario row has unexpected fields.")
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
