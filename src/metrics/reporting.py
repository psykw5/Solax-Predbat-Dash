"""Dataset coverage and validation reports for processed SolaX data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from metrics.energy import EnergyMetrics


@dataclass(frozen=True)
class CoverageStats:
    earliest_timestamp: pd.Timestamp | None
    latest_timestamp: pd.Timestamp | None
    source_files: int
    calendar_days_covered: int
    expected_intervals: int
    observed_intervals: int
    missing_interval_events: int
    missing_dates: list[str]


def generate_solax_dataset_report(processed_dir: Path | str = Path("data/processed/solax")) -> Path:
    processed = Path(processed_dir)
    intervals = pd.read_parquet(processed / "solax_intervals.parquet")
    validation = read_csv_or_empty(processed / "validation_report.csv")
    daily_validation = read_csv_or_empty(processed / "daily_validation_report.csv")
    metadata = read_csv_or_empty(processed / "report_metadata.csv")
    summary = read_json_or_empty(processed / "ingestion_summary.json")
    metrics = EnergyMetrics(processed / "solax_intervals.parquet")

    coverage = build_coverage(intervals, validation, summary)
    monthly = build_monthly_metrics(metrics, intervals)
    annual = build_annual_metrics(metrics, intervals)
    monthly.to_csv(processed / "monthly_metrics_summary.csv", index=False)
    annual.to_csv(processed / "annual_metrics_summary.csv", index=False)

    report_path = processed / "dataset_coverage_validation_report.md"
    report_path.write_text(
        render_report(
            coverage,
            intervals,
            validation,
            daily_validation,
            metadata,
            monthly,
            annual,
            metrics,
            summary,
        ),
        encoding="utf-8",
    )
    return report_path


def read_csv_or_empty(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def read_json_or_empty(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def build_coverage(
    intervals: pd.DataFrame, validation: pd.DataFrame, summary: dict[str, object]
) -> CoverageStats:
    if intervals.empty:
        return CoverageStats(None, None, 0, 0, 0, 0, 0, [])

    starts = pd.to_datetime(intervals["interval_start"])
    ends = pd.to_datetime(intervals["interval_end"])
    dates = pd.to_datetime(intervals["date"]).dt.date
    earliest = starts.min()
    latest = ends.max()
    full_dates = pd.date_range(dates.min(), dates.max(), freq="D").date
    observed_dates = set(dates)
    missing_dates = [day.isoformat() for day in full_dates if day not in observed_dates]
    expected = int((latest - earliest) / pd.Timedelta(minutes=5))
    missing_events = (
        int((validation["event_type"] == "missing_timestamp").sum())
        if not validation.empty and "event_type" in validation
        else 0
    )
    return CoverageStats(
        earliest_timestamp=earliest,
        latest_timestamp=latest,
        source_files=int(summary.get("files_found", intervals["source_filename"].nunique())),
        calendar_days_covered=len(observed_dates),
        expected_intervals=expected,
        observed_intervals=len(intervals),
        missing_interval_events=missing_events,
        missing_dates=missing_dates,
    )


def build_monthly_metrics(metrics: EnergyMetrics, intervals: pd.DataFrame) -> pd.DataFrame:
    months = (
        pd.to_datetime(intervals["interval_start"])
        .dt.to_period("M")
        .drop_duplicates()
        .sort_values()
    )
    rows = []
    for period in months:
        summary = metrics.monthly_summary(period.year, period.month)
        rows.append(
            {
                "year": summary.year,
                "month": summary.month,
                "generation_kwh": summary.generation_kwh,
                "import_kwh": summary.import_kwh,
                "export_kwh": summary.export_kwh,
                "household_consumption_kwh": summary.household_consumption_kwh,
                "reported_inverter_consumption_kwh": summary.reported_inverter_consumption_kwh,
                "self_consumed_kwh": summary.self_consumed_kwh,
                "self_consumption_percent": summary.self_consumption_percent,
                "interval_count": summary.interval_count,
                "quality_flagged_interval_count": summary.quality_flagged_interval_count,
            }
        )
    return pd.DataFrame(rows)


def build_annual_metrics(metrics: EnergyMetrics, intervals: pd.DataFrame) -> pd.DataFrame:
    years = sorted(pd.to_datetime(intervals["interval_start"]).dt.year.drop_duplicates())
    rows = []
    for year in years:
        summary = metrics.annual_summary(int(year))
        rows.append(
            {
                "year": summary.year,
                "generation_kwh": summary.generation_kwh,
                "import_kwh": summary.import_kwh,
                "export_kwh": summary.export_kwh,
                "household_consumption_kwh": summary.household_consumption_kwh,
                "reported_inverter_consumption_kwh": summary.reported_inverter_consumption_kwh,
                "self_consumed_kwh": summary.self_consumed_kwh,
                "self_consumption_percent": summary.self_consumption_percent,
                "interval_count": summary.interval_count,
                "quality_flagged_interval_count": summary.quality_flagged_interval_count,
            }
        )
    return pd.DataFrame(rows)


def render_report(
    coverage: CoverageStats,
    intervals: pd.DataFrame,
    validation: pd.DataFrame,
    daily_validation: pd.DataFrame,
    metadata: pd.DataFrame,
    monthly: pd.DataFrame,
    annual: pd.DataFrame,
    metrics: EnergyMetrics,
    summary: dict[str, object],
) -> str:
    earliest = "" if coverage.earliest_timestamp is None else str(coverage.earliest_timestamp)
    latest = "" if coverage.latest_timestamp is None else str(coverage.latest_timestamp)
    lifetime_start = coverage.earliest_timestamp.to_pydatetime()
    lifetime_end = coverage.latest_timestamp.to_pydatetime()
    generation = metrics.total_generation(lifetime_start, lifetime_end)
    imported = metrics.total_import(lifetime_start, lifetime_end)
    exported = metrics.total_export(lifetime_start, lifetime_end)
    household_consumption = metrics.total_household_consumption(lifetime_start, lifetime_end)
    reported_inverter_consumption = metrics.total_reported_inverter_consumption(
        lifetime_start, lifetime_end
    )
    self_consumption = metrics.self_consumption(lifetime_start, lifetime_end)

    event_counts = event_count_table(validation)
    daily_table = daily_validation_table(daily_validation)
    annual_table = dataframe_markdown(annual.round(3))
    monthly_table = dataframe_markdown(monthly.round(3))
    missing_dates = ", ".join(coverage.missing_dates[:60]) if coverage.missing_dates else "None"
    if len(coverage.missing_dates) > 60:
        missing_dates += f", ... ({len(coverage.missing_dates)} total)"

    return "\n".join(
        [
            "# SolaX Dataset Coverage and Validation Report",
            "",
            "Generated from processed SolaX ETL outputs. Source filenames and source metadata are sanitized in this report.",
            "",
            "## Coverage",
            "",
            f"- Earliest timestamp: `{earliest}`",
            f"- Latest timestamp: `{latest}`",
            f"- Source files: `{coverage.source_files}`",
            f"- Source files processed: `{summary.get('files_processed', 'unknown')}`",
            f"- Source files failed validation/read: `{summary.get('files_failed', 'unknown')}`",
            f"- Calendar days covered: `{coverage.calendar_days_covered}`",
            f"- Expected five-minute intervals between earliest and latest timestamp: `{coverage.expected_intervals}`",
            f"- Observed canonical intervals: `{coverage.observed_intervals}`",
            f"- Missing timestamp events: `{coverage.missing_interval_events}`",
            f"- Missing dates: {missing_dates}",
            "",
            "## Lifetime Metrics",
            "",
            f"- Lifetime generation: `{generation.kwh:.3f} kWh`",
            f"- Lifetime import: `{imported.kwh:.3f} kWh`",
            f"- Lifetime export: `{exported.kwh:.3f} kWh`",
            f"- Lifetime household consumption: `{household_consumption.kwh:.3f} kWh`",
            f"- Reported inverter consumption: `{reported_inverter_consumption.kwh:.3f} kWh`",
            f"- Estimated self-consumption: `{self_consumption.self_consumed_kwh:.3f} kWh`",
            f"- Estimated self-consumption percentage: `{self_consumption.self_consumption_percent:.2f}%`",
            "",
            "## Validation Events by Type and Severity",
            "",
            event_counts,
            "",
            "## Duplicate and Overlap Checks",
            "",
            duplicate_overlap_text(validation),
            "",
            "## Daylight-Saving Transition Handling",
            "",
            dst_text(validation),
            "",
            "## Counter Resets, Rollbacks and Negative Differences",
            "",
            counter_text(validation),
            "",
            "## Daily Reconstruction Check",
            "",
            daily_table,
            "",
            "## Annual Summary",
            "",
            annual_table,
            "",
            "## Monthly Summary",
            "",
            monthly_table,
            "",
            "## Reliability Statement",
            "",
            reliability_statement(daily_validation, validation),
            "",
            "## Metadata Extraction",
            "",
            f"- Metadata rows: `{len(metadata)}`",
            "- Report metadata output contains sanitized source names, reporting period bounds, import timestamps, row counts, hashes, and redacted plant-name text where needed.",
        ]
    )


def event_count_table(validation: pd.DataFrame) -> str:
    if validation.empty:
        return "No validation events."
    grouped = (
        validation.groupby(["event_type", "severity"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values(["event_type", "severity"])
    )
    return dataframe_markdown(grouped)


def daily_validation_table(daily_validation: pd.DataFrame) -> str:
    if daily_validation.empty:
        return "No daily validation rows."
    df = daily_validation.copy()
    df["matches_final_cumulative"] = df["matches_final_cumulative"].astype(str)
    grouped = (
        df.groupby(["field", "matches_final_cumulative"], dropna=False)
        .agg(
            days=("date", "count"),
            max_abs_difference_kwh=("difference_kwh", lambda values: round(values.abs().max(), 6)),
        )
        .reset_index()
        .sort_values(["field", "matches_final_cumulative"])
    )
    return dataframe_markdown(grouped)


def duplicate_overlap_text(validation: pd.DataFrame) -> str:
    counts = count_events(
        validation, ["duplicate_interval", "overlapping_interval", "overlapping_reporting_period"]
    )
    if not counts:
        return "No duplicate interval, overlapping interval, or overlapping reporting-period events were reported."
    return "\n".join(f"- `{name}`: `{count}`" for name, count in counts.items())


def dst_text(validation: pd.DataFrame) -> str:
    count = count_events(validation, ["daylight_saving_transition"]).get(
        "daylight_saving_transition", 0
    )
    if count == 0:
        return "No ambiguous or nonexistent `Europe/London` local timestamps were detected. The ETL checks for DST issues and would flag affected rows as validation events."
    return f"`{count}` daylight-saving transition timestamp events were detected and flagged."


def counter_text(validation: pd.DataFrame) -> str:
    counts = count_events(validation, ["midnight_reset", "counter_rollback", "negative_difference"])
    if not counts:
        return "No counter reset, rollback, or negative-difference events were reported."
    return "\n".join(f"- `{name}`: `{count}`" for name, count in counts.items())


def count_events(validation: pd.DataFrame, names: list[str]) -> dict[str, int]:
    if validation.empty or "event_type" not in validation:
        return {}
    counts = validation["event_type"].value_counts().to_dict()
    return {name: int(counts[name]) for name in names if name in counts}


def reliability_statement(daily_validation: pd.DataFrame, validation: pd.DataFrame) -> str:
    if daily_validation.empty:
        return "No daily reconstruction evidence is available, so all measures remain provisional."
    df = daily_validation.copy()
    mismatch_counts = (
        df[df["matches_final_cumulative"].astype(str) != "True"].groupby("field").size().to_dict()
    )
    reliable = []
    provisional = []
    field_labels = {
        "pv_yield_kwh": "generation",
        "exported_energy_kwh": "export",
        "imported_energy_kwh": "import",
        "consumed_energy_kwh": "reported inverter consumption",
        "inverter_output_kwh": "inverter output",
    }
    for field, label in field_labels.items():
        if mismatch_counts.get(field, 0) == 0:
            reliable.append(label)
        else:
            provisional.append(f"{label} ({mismatch_counts[field]} daily mismatches)")
    parts = []
    if reliable:
        parts.append("Sufficiently reliable for dashboard totals: " + ", ".join(reliable) + ".")
    if provisional:
        parts.append("Provisional pending review: " + ", ".join(provisional) + ".")
    if count_events(validation, ["counter_rollback"]):
        parts.append(
            "Counter rollbacks are preserved as quality flags; affected interval values should be treated with caution in charts and aggregations."
        )
    return "\n\n".join(parts)


def dataframe_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "No rows."
    safe = frame.fillna("")
    columns = [str(column) for column in safe.columns]
    rows = [
        [str(value) for value in row]
        for row in safe.astype(object).itertuples(index=False, name=None)
    ]
    widths = [
        max(len(columns[index]), *(len(row[index]) for row in rows))
        for index in range(len(columns))
    ]
    header = (
        "| "
        + " | ".join(columns[index].ljust(widths[index]) for index in range(len(columns)))
        + " |"
    )
    separator = "| " + " | ".join("-" * widths[index] for index in range(len(columns))) + " |"
    body = [
        "| " + " | ".join(row[index].ljust(widths[index]) for index in range(len(columns))) + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def main() -> int:
    path = generate_solax_dataset_report()
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
