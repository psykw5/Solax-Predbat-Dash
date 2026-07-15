"""Private tariff scenario pricing and battery simulation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tariffs.models import BatteryAssumptions, ScenarioResult, TariffQualityEvent, TariffScenario

DEFAULT_SOLAX_PATH = Path("data/processed/solax/solax_intervals.parquet")
DEFAULT_OUTPUT_DIR = Path("data/processed/tariff_whatif")
LONDON_TZ = "Europe/London"


def measured_half_hours_from_solax(solax: pd.DataFrame) -> pd.DataFrame:
    frame = solax.copy()
    frame["interval_start"] = pd.to_datetime(frame["interval_start"])
    frame["interval_end"] = pd.to_datetime(frame["interval_end"])
    localized = frame["interval_start"].dt.tz_localize(
        LONDON_TZ, ambiguous="NaT", nonexistent="NaT"
    )
    frame = frame[localized.notna()].copy()
    localized = localized[localized.notna()]
    frame["settlement_start_local"] = localized.dt.floor("30min")
    frame["settlement_start_utc"] = frame["settlement_start_local"].dt.tz_convert(UTC)
    frame["settlement_end_utc"] = frame["settlement_start_utc"] + pd.Timedelta(minutes=30)
    frame["self_consumed_generation_kwh"] = (
        frame["pv_yield_kwh"] - frame["exported_energy_kwh"]
    ).clip(lower=0)
    frame["household_demand_kwh"] = (
        frame["self_consumed_generation_kwh"] + frame["imported_energy_kwh"]
    )
    return (
        frame.groupby(["settlement_start_utc", "settlement_end_utc"], as_index=False)
        .agg(
            generation_kwh=("pv_yield_kwh", "sum"),
            grid_import_kwh=("imported_energy_kwh", "sum"),
            grid_export_kwh=("exported_energy_kwh", "sum"),
            household_demand_kwh=("household_demand_kwh", "sum"),
            source_interval_count=("interval_start", "count"),
        )
        .sort_values("settlement_start_utc")
        .reset_index(drop=True)
    )


def actual_flow_replay(
    measured: pd.DataFrame,
    scenario: TariffScenario,
    import_rates: pd.DataFrame,
    export_rates: pd.DataFrame,
    flux_baseline_cost_gbp: float | None = None,
) -> ScenarioResult:
    joined, quality = join_rates(measured, import_rates, export_rates)
    covered = joined["import_rate_p_per_kwh"].notna() & joined["export_rate_p_per_kwh"].notna()
    included = joined[covered].copy()
    import_cost = float(
        (included["grid_import_kwh"] * included["import_rate_p_per_kwh"] / 100).sum()
    )
    export_income = float(
        (included["grid_export_kwh"] * included["export_rate_p_per_kwh"] / 100).sum()
    )
    standing = standing_charges(included, scenario.standing_charge_p_per_day)
    no_solar = (
        float((included["household_demand_kwh"] * included["import_rate_p_per_kwh"] / 100).sum())
        + standing
    )
    net = import_cost + standing - export_income
    coverage = coverage_percent(covered)
    return ScenarioResult(
        scenario=scenario.name,
        strategy="actual_flow_replay",
        eligibility_status=scenario.eligibility_status,
        import_energy_cost_gbp=round(import_cost, 2),
        export_income_gbp=round(export_income, 2),
        standing_charges_gbp=round(standing, 2),
        net_electricity_cost_gbp=round(net, 2),
        no_solar_cost_gbp=round(no_solar, 2),
        financial_benefit_vs_no_solar_gbp=round(no_solar - net, 2),
        difference_vs_flux_gbp=difference(net, flux_baseline_cost_gbp),
        annualised_difference_vs_flux_gbp=None
        if coverage < 99
        else annualised_difference(net, flux_baseline_cost_gbp, included),
        tariff_coverage_percentage=coverage,
        cheap_import_percentage=percentage_in_rate_bucket(
            included, "grid_import_kwh", "import_rate_p_per_kwh", low=True
        ),
        high_value_export_percentage=percentage_in_rate_bucket(
            included, "grid_export_kwh", "export_rate_p_per_kwh", low=False
        ),
        data_quality_status="complete" if not quality else "warnings",
        assumptions={"vat_included": scenario.vat_included},
    )


def optimise_battery_dispatch(
    measured: pd.DataFrame,
    scenario: TariffScenario,
    import_rates: pd.DataFrame,
    export_rates: pd.DataFrame,
    battery: BatteryAssumptions | None = None,
    flux_baseline_cost_gbp: float | None = None,
) -> tuple[pd.DataFrame, ScenarioResult]:
    assumptions = battery or BatteryAssumptions()
    joined, _ = join_rates(measured, import_rates, export_rates)
    covered = joined["import_rate_p_per_kwh"].notna() & joined["export_rate_p_per_kwh"].notna()
    frame = joined[covered].copy().reset_index(drop=True)
    if frame.empty:
        return frame, empty_optimised_result(scenario)

    cheap_threshold = frame["import_rate_p_per_kwh"].quantile(0.25)
    high_threshold = frame["import_rate_p_per_kwh"].quantile(0.75)
    high_export_threshold = frame["export_rate_p_per_kwh"].quantile(0.75)
    soc = min(
        max(assumptions.initial_soc_kwh, assumptions.minimum_soc_kwh), assumptions.maximum_soc_kwh
    )
    rows: list[dict[str, Any]] = []
    throughput = 0.0
    for row in frame.itertuples(index=False):
        duration_hours = (
            pd.Timestamp(row.settlement_end_utc) - pd.Timestamp(row.settlement_start_utc)
        ).total_seconds() / 3600
        charge_limit = assumptions.charge_power_kw * duration_hours
        discharge_limit = assumptions.discharge_power_kw * duration_hours
        demand = float(row.household_demand_kwh)
        generation = float(row.generation_kwh)
        import_rate = float(row.import_rate_p_per_kwh)
        export_rate = float(row.export_rate_p_per_kwh)

        direct_solar = min(demand, generation)
        remaining_demand = max(demand - direct_solar, 0.0)
        surplus_solar = max(generation - direct_solar, 0.0)
        charge_from_solar = min(
            surplus_solar * assumptions.charge_efficiency,
            charge_limit,
            assumptions.maximum_soc_kwh - soc,
        )
        soc += charge_from_solar
        throughput += charge_from_solar
        export_kwh = max(surplus_solar - charge_from_solar / assumptions.charge_efficiency, 0.0)
        grid_import = 0.0
        grid_charge = 0.0

        if import_rate >= high_threshold and remaining_demand > 0:
            available_discharge = min(
                discharge_limit,
                max(soc - assumptions.minimum_soc_kwh, 0.0),
                remaining_demand / assumptions.discharge_efficiency,
            )
            delivered = available_discharge * assumptions.discharge_efficiency
            soc -= available_discharge
            throughput += available_discharge
            remaining_demand -= delivered

        grid_import += remaining_demand

        can_grid_charge = (
            assumptions.allow_grid_to_battery
            and import_rate <= cheap_threshold
            and not (export_kwh > 0 and not assumptions.allow_simultaneous_import_export_arbitrage)
        )
        if can_grid_charge:
            grid_charge = min(
                charge_limit,
                assumptions.maximum_soc_kwh - soc,
            )
            if grid_charge > 0:
                grid_import += grid_charge / assumptions.charge_efficiency
                soc += grid_charge
                throughput += grid_charge

        if (
            assumptions.allow_battery_export
            and export_rate >= high_export_threshold
            and grid_import == 0
        ):
            export_discharge = min(discharge_limit, max(soc - assumptions.minimum_soc_kwh, 0.0))
            soc -= export_discharge
            throughput += export_discharge
            export_kwh += export_discharge * assumptions.discharge_efficiency

        rows.append(
            {
                "settlement_start_utc": row.settlement_start_utc,
                "settlement_end_utc": row.settlement_end_utc,
                "simulated_grid_import_kwh": round(grid_import, 6),
                "simulated_grid_export_kwh": round(export_kwh, 6),
                "battery_soc_kwh": round(soc, 6),
                "grid_charge_kwh": round(grid_charge, 6),
                "import_rate_p_per_kwh": import_rate,
                "export_rate_p_per_kwh": export_rate,
            }
        )
    simulated = pd.DataFrame(rows)
    import_cost = float(
        (simulated["simulated_grid_import_kwh"] * simulated["import_rate_p_per_kwh"] / 100).sum()
    )
    export_income = float(
        (simulated["simulated_grid_export_kwh"] * simulated["export_rate_p_per_kwh"] / 100).sum()
    )
    standing = standing_charges(simulated, scenario.standing_charge_p_per_day)
    no_solar = (
        float((frame["household_demand_kwh"] * frame["import_rate_p_per_kwh"] / 100).sum())
        + standing
    )
    net = import_cost + standing - export_income
    coverage = coverage_percent(covered)
    result = ScenarioResult(
        scenario=scenario.name,
        strategy="optimised_battery_simulation",
        eligibility_status=scenario.eligibility_status,
        import_energy_cost_gbp=round(import_cost, 2),
        export_income_gbp=round(export_income, 2),
        standing_charges_gbp=round(standing, 2),
        net_electricity_cost_gbp=round(net, 2),
        no_solar_cost_gbp=round(no_solar, 2),
        financial_benefit_vs_no_solar_gbp=round(no_solar - net, 2),
        difference_vs_flux_gbp=difference(net, flux_baseline_cost_gbp),
        annualised_difference_vs_flux_gbp=None
        if coverage < 99
        else annualised_difference(net, flux_baseline_cost_gbp, frame),
        tariff_coverage_percentage=coverage,
        battery_throughput_kwh=round(throughput, 3),
        equivalent_full_battery_cycles=round(throughput / assumptions.usable_capacity_kwh, 3),
        cheap_import_percentage=percentage_in_rate_bucket(
            simulated, "simulated_grid_import_kwh", "import_rate_p_per_kwh", low=True
        ),
        high_value_export_percentage=percentage_in_rate_bucket(
            simulated, "simulated_grid_export_kwh", "export_rate_p_per_kwh", low=False
        ),
        data_quality_status="complete",
        assumptions=assumptions.model_dump(),
    )
    return simulated, result


def compare_scenarios(
    measured: pd.DataFrame,
    scenarios: dict[str, tuple[TariffScenario, pd.DataFrame, pd.DataFrame]],
    battery: BatteryAssumptions | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> pd.DataFrame:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    flux_cost: float | None = None
    replay_results: dict[str, ScenarioResult] = {}
    for name, (scenario, import_rates, export_rates) in scenarios.items():
        replay = actual_flow_replay(measured, scenario, import_rates, export_rates)
        replay_results[name] = replay
        if name == "octopus_flux":
            flux_cost = replay.net_electricity_cost_gbp
    for name, (scenario, import_rates, export_rates) in scenarios.items():
        replay = actual_flow_replay(measured, scenario, import_rates, export_rates, flux_cost)
        simulated, optimised = optimise_battery_dispatch(
            measured, scenario, import_rates, export_rates, battery, flux_cost
        )
        rows.extend([replay.model_dump(), optimised.model_dump()])
        simulated.to_parquet(output_dir / f"{name}_half_hourly_simulated.parquet", index=False)
    summary = pd.DataFrame(rows)
    summary.to_csv(output_dir / "scenario_summary.csv", index=False)
    (output_dir / "comparison.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return summary


def join_rates(
    measured: pd.DataFrame, import_rates: pd.DataFrame, export_rates: pd.DataFrame
) -> tuple[pd.DataFrame, list[TariffQualityEvent]]:
    frame = measured.copy()
    frame["settlement_start_utc"] = pd.to_datetime(frame["settlement_start_utc"], utc=True)
    frame["settlement_end_utc"] = pd.to_datetime(frame["settlement_end_utc"], utc=True)
    joined = join_rate_direction(frame, import_rates, "import")
    joined = join_rate_direction(joined, export_rates, "export")
    return joined, validate_rate_coverage(joined)


def join_rate_direction(
    measured: pd.DataFrame, rates: pd.DataFrame, direction: str
) -> pd.DataFrame:
    if rates.empty:
        frame = measured.copy()
        frame[f"{direction}_rate_p_per_kwh"] = pd.NA
        return frame
    rate_frame = rates.copy()
    rate_frame["valid_from"] = pd.to_datetime(rate_frame["valid_from"], utc=True)
    rate_frame["valid_to"] = pd.to_datetime(rate_frame["valid_to"], utc=True)
    rate_frame = rate_frame.sort_values(["valid_from", "valid_to", "value_inc_vat"])
    merged = pd.merge_asof(
        measured.sort_values("settlement_start_utc"),
        rate_frame[["valid_from", "valid_to", "value_inc_vat"]].sort_values("valid_from"),
        left_on="settlement_start_utc",
        right_on="valid_from",
        direction="backward",
    )
    covered = merged["valid_to"].isna() | (merged["valid_to"] >= merged["settlement_end_utc"])
    merged.loc[~covered, "value_inc_vat"] = pd.NA
    return merged.drop(columns=["valid_from", "valid_to"]).rename(
        columns={"value_inc_vat": f"{direction}_rate_p_per_kwh"}
    )


def validate_rate_tables(
    rates: pd.DataFrame, scenario: str, direction: str
) -> list[TariffQualityEvent]:
    if rates.empty:
        return [
            TariffQualityEvent(
                event_type=f"missing_{direction}_rates",
                severity="error",
                scenario=scenario,
                message="No rate intervals supplied.",
            )
        ]
    frame = rates.copy().sort_values("valid_from")
    frame["valid_from"] = pd.to_datetime(frame["valid_from"], utc=True)
    frame["valid_to"] = pd.to_datetime(frame["valid_to"], utc=True)
    events: list[TariffQualityEvent] = []
    duplicate_count = int(frame.duplicated(["valid_from", "valid_to", "value_inc_vat"]).sum())
    if duplicate_count:
        events.append(
            TariffQualityEvent(
                event_type=f"duplicate_{direction}_rates",
                severity="warning",
                scenario=scenario,
                message="Duplicate rate intervals present.",
                count=duplicate_count,
            )
        )
    previous_end = None
    overlap_count = 0
    for row in frame.itertuples(index=False):
        if previous_end is not None and row.valid_from < previous_end:
            overlap_count += 1
        previous_end = row.valid_to
    if overlap_count:
        events.append(
            TariffQualityEvent(
                event_type=f"overlapping_{direction}_rates",
                severity="error",
                scenario=scenario,
                message="Overlapping rate intervals present.",
                count=overlap_count,
            )
        )
    return events


def validate_scenario_pairing(scenario: TariffScenario) -> TariffQualityEvent | None:
    if scenario.eligibility_status not in {"eligible", "historical_only"}:
        return TariffQualityEvent(
            event_type="tariff_pairing_not_actionable",
            severity="warning",
            scenario=scenario.name,
            message=f"Scenario eligibility is {scenario.eligibility_status}.",
        )
    return None


def validate_rate_coverage(frame: pd.DataFrame) -> list[TariffQualityEvent]:
    missing_import = int(frame["import_rate_p_per_kwh"].isna().sum())
    missing_export = int(frame["export_rate_p_per_kwh"].isna().sum())
    events = []
    if missing_import:
        events.append(
            TariffQualityEvent(
                event_type="missing_import_coverage",
                severity="error",
                scenario="unknown",
                message="Measured intervals without import rates.",
                count=missing_import,
            )
        )
    if missing_export:
        events.append(
            TariffQualityEvent(
                event_type="missing_export_coverage",
                severity="error",
                scenario="unknown",
                message="Measured intervals without export rates.",
                count=missing_export,
            )
        )
    return events


def prime_export_window_mask(
    timestamps: pd.Series, start_hour: int = 11, end_hour: int = 16
) -> pd.Series:
    local = pd.to_datetime(timestamps, utc=True).dt.tz_convert(LONDON_TZ)
    return (local.dt.hour >= start_hour) & (local.dt.hour < end_hour)


def coverage_percent(mask: pd.Series) -> float:
    return round(float(mask.mean() * 100), 4) if len(mask) else 0.0


def standing_charges(frame: pd.DataFrame, standing_charge_p_per_day: float) -> float:
    if frame.empty:
        return 0.0
    starts = pd.to_datetime(frame["settlement_start_utc"], utc=True)
    days = starts.dt.floor("D").nunique()
    return float(days * standing_charge_p_per_day / 100)


def difference(net_cost: float, flux_baseline_cost_gbp: float | None) -> float | None:
    if flux_baseline_cost_gbp is None:
        return None
    return round(net_cost - flux_baseline_cost_gbp, 2)


def annualised_difference(
    net_cost: float, flux_baseline_cost_gbp: float | None, frame: pd.DataFrame
) -> float | None:
    if flux_baseline_cost_gbp is None or frame.empty:
        return None
    rounded_difference = round(net_cost - flux_baseline_cost_gbp, 2)
    if rounded_difference == 0:
        return 0.0
    starts = pd.to_datetime(frame["settlement_start_utc"], utc=True)
    days = max((starts.max() - starts.min()).days + 1, 1)
    return round(rounded_difference / days * 365, 2)


def percentage_in_rate_bucket(
    frame: pd.DataFrame, energy_column: str, rate_column: str, low: bool
) -> float | None:
    energy = frame[energy_column].sum()
    if energy <= 0:
        return None
    threshold = frame[rate_column].quantile(0.25 if low else 0.75)
    mask = frame[rate_column] <= threshold if low else frame[rate_column] >= threshold
    return round(float(frame.loc[mask, energy_column].sum() / energy * 100), 4)


def empty_optimised_result(scenario: TariffScenario) -> ScenarioResult:
    return ScenarioResult(
        scenario=scenario.name,
        strategy="optimised_battery_simulation",
        eligibility_status=scenario.eligibility_status,
        import_energy_cost_gbp=0,
        export_income_gbp=0,
        standing_charges_gbp=0,
        net_electricity_cost_gbp=0,
        no_solar_cost_gbp=0,
        financial_benefit_vs_no_solar_gbp=0,
        tariff_coverage_percentage=0,
        data_quality_status="no_supported_intervals",
    )


def scenario_table(results: pd.DataFrame) -> str:
    pivot = results.pivot(index="scenario", columns="strategy", values="net_electricity_cost_gbp")
    lines = ["| Scenario | Actual-flow cost | Optimised cost | Difference vs Flux | Eligibility |"]
    lines.append("|---|---:|---:|---:|---|")
    for scenario in pivot.index:
        subset = results[results["scenario"] == scenario]
        eligibility = str(subset["eligibility_status"].iloc[0])
        difference_value = subset["difference_vs_flux_gbp"].dropna()
        difference_text = "" if difference_value.empty else f"{difference_value.iloc[0]:.2f}"
        lines.append(
            f"| {scenario} | {pivot.loc[scenario].get('actual_flow_replay', 0):.2f} | "
            f"{pivot.loc[scenario].get('optimised_battery_simulation', 0):.2f} | "
            f"{difference_text} | {eligibility} |"
        )
    return "\n".join(lines)


def default_scenarios(retrieval_date: datetime | None = None) -> list[TariffScenario]:
    retrieved = retrieval_date or datetime.now(UTC)
    return [
        TariffScenario(
            name="octopus_flux",
            import_product_code="FLUX",
            export_product_code="FLUX-EXPORT",
            eligibility_status="eligible",
            eligibility_evidence="Official Octopus Flux page describes a combined import/export tariff for solar and battery owners.",
            retrieval_date=retrieved,
            standing_charge_p_per_day=0,
        ),
        TariffScenario(
            name="agile_import_agile_outgoing",
            import_product_code="AGILE",
            export_product_code="AGILE-OUTGOING",
            eligibility_status="compatibility_unconfirmed",
            eligibility_evidence="Agile import and Agile Outgoing export are separate smart tariffs; pairing must be confirmed by Octopus.",
            retrieval_date=retrieved,
            standing_charge_p_per_day=0,
        ),
        TariffScenario(
            name="agile_import_flat_outgoing",
            import_product_code="AGILE",
            export_product_code="OUTGOING-FIX",
            eligibility_status="compatibility_unconfirmed",
            eligibility_evidence="Agile import and fixed Outgoing export compatibility must be confirmed by Octopus.",
            retrieval_date=retrieved,
            standing_charge_p_per_day=0,
        ),
        TariffScenario(
            name="standard_import_prime_outgoing",
            import_product_code="VAR",
            export_product_code="PRIME-OUTGOING",
            eligibility_status="compatibility_unconfirmed",
            eligibility_evidence="Prime Outgoing availability and pairing with a standard import tariff must be confirmed from current Octopus sources.",
            retrieval_date=retrieved,
            standing_charge_p_per_day=0,
        ),
    ]


def future_ev_scenario(retrieval_date: datetime | None = None) -> TariffScenario:
    retrieved = retrieval_date or datetime.now(UTC)
    return TariffScenario(
        name="intelligent_octopus_go_future_ev",
        import_product_code="INTELLIGENT-OCTOPUS-GO",
        export_product_code="OUTGOING",
        eligibility_status="future_unmodelled",
        eligibility_evidence=(
            "Future EV scenario only. It requires a separate private EV charging data stream "
            "and charger/vehicle compatibility confirmation before comparison or ranking."
        ),
        retrieval_date=retrieved,
        standing_charge_p_per_day=0,
        notes=(
            "Do not publish private EV charging timestamps, identifiers or charging behaviour.",
            "Not ranked against non-EV scenarios until measured EV load can be separated.",
        ),
    )
