"""Financial transformation joining SolaX intervals to Octopus tariff rates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from models.octopus import FinancialSummary

LONDON_TZ = "Europe/London"
DEFAULT_SOLAX_PATH = Path("data/processed/solax/solax_intervals.parquet")
DEFAULT_OCTOPUS_DIR = Path("data/processed/octopus")
DEFAULT_FINANCIAL_DIR = Path("data/processed/financial")


def run_financial_pipeline(
    solax_path: Path = DEFAULT_SOLAX_PATH,
    octopus_dir: Path = DEFAULT_OCTOPUS_DIR,
    output_dir: Path = DEFAULT_FINANCIAL_DIR,
) -> FinancialSummary:
    output_dir.mkdir(parents=True, exist_ok=True)
    solax = pd.read_parquet(solax_path)
    agreements = pd.read_parquet(octopus_dir / "tariff_agreements.parquet")
    import_rates = prepare_active_rates(
        pd.read_parquet(octopus_dir / "import_unit_rates.parquet"), agreements, "import"
    )
    export_rates = prepare_active_rates(
        pd.read_parquet(octopus_dir / "export_unit_rates.parquet"), agreements, "export"
    )

    half_hourly, energy_quality = aggregate_solax_to_settlement_half_hours(solax)
    import_quality = validate_rates(import_rates, "import")
    export_quality = validate_rates(export_rates, "export")
    agreement_quality = validate_agreements(agreements)

    half_hourly = annotate_export_agreement_status(half_hourly, agreements)
    joined = join_financial_rates(half_hourly, import_rates, export_rates)
    joined, join_quality = calculate_financial_values(joined)
    quality = pd.concat(
        [energy_quality, import_quality, export_quality, agreement_quality, join_quality],
        ignore_index=True,
    )

    monthly = build_period_summary(joined, "M")
    annual = build_period_summary(joined, "Y")
    summary = build_lifetime_summary(joined)

    write_parquet(joined, output_dir / "half_hourly_financials.parquet")
    joined.to_csv(output_dir / "half_hourly_financials.csv", index=False)
    monthly.to_csv(output_dir / "monthly_financial_summary.csv", index=False)
    annual.to_csv(output_dir / "annual_financial_summary.csv", index=False)
    quality.to_csv(output_dir / "financial_data_quality_report.csv", index=False)
    (output_dir / "lifetime_summary.json").write_text(
        json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def aggregate_solax_to_settlement_half_hours(
    solax: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = solax.copy()
    frame["interval_start"] = pd.to_datetime(frame["interval_start"])
    frame["interval_end"] = pd.to_datetime(frame["interval_end"])
    frame["duration_minutes"] = (
        frame["interval_end"] - frame["interval_start"]
    ).dt.total_seconds() / 60

    quality_events: list[dict[str, object]] = []
    negative_mask = (
        (frame["pv_yield_kwh"] < 0)
        | (frame["exported_energy_kwh"] < 0)
        | (frame["consumed_energy_kwh"] < 0)
        | (frame["imported_energy_kwh"] < 0)
    )
    if negative_mask.any():
        quality_events.append(
            event("negative_energy_value", "error", int(negative_mask.sum()), "Intervals excluded.")
        )
    impossible_mask = frame["duration_minutes"] <= 0
    if impossible_mask.any():
        quality_events.append(
            event(
                "impossible_interval_duration",
                "error",
                int(impossible_mask.sum()),
                "Intervals excluded.",
            )
        )
    frame = frame[~negative_mask & ~impossible_mask].copy()

    localized_start = frame["interval_start"].dt.tz_localize(
        LONDON_TZ, ambiguous="NaT", nonexistent="NaT"
    )
    localized_end = frame["interval_end"].dt.tz_localize(
        LONDON_TZ, ambiguous="NaT", nonexistent="NaT"
    )
    dst_mask = localized_start.isna() | localized_end.isna()
    if dst_mask.any():
        quality_events.append(
            event(
                "dst_transition_interval",
                "warning",
                int(dst_mask.sum()),
                "Ambiguous or nonexistent local intervals excluded.",
            )
        )
    frame = frame[~dst_mask].copy()
    localized_start = localized_start[~dst_mask]

    frame["settlement_start_local"] = localized_start.dt.floor("30min")
    frame["settlement_end_local"] = frame["settlement_start_local"] + pd.Timedelta(minutes=30)
    frame["settlement_start_utc"] = frame["settlement_start_local"].dt.tz_convert(UTC)
    frame["settlement_end_utc"] = frame["settlement_end_local"].dt.tz_convert(UTC)
    frame["estimated_self_consumed_solar_kwh"] = (
        frame["pv_yield_kwh"] - frame["exported_energy_kwh"]
    ).clip(lower=0)

    grouped = (
        frame.groupby(
            [
                "settlement_start_local",
                "settlement_end_local",
                "settlement_start_utc",
                "settlement_end_utc",
            ],
            dropna=False,
        )
        .agg(
            generation_kwh=("pv_yield_kwh", "sum"),
            export_kwh=("exported_energy_kwh", "sum"),
            estimated_self_consumed_solar_kwh=("estimated_self_consumed_solar_kwh", "sum"),
            source_interval_count=("interval_start", "count"),
            source_quality_flags=("quality_flags", join_flags),
        )
        .reset_index()
        .sort_values("settlement_start_utc")
        .reset_index(drop=True)
    )
    grouped["energy_status"] = "reconstructed"
    return grouped, pd.DataFrame(quality_events)


def join_financial_rates(
    half_hourly: pd.DataFrame, import_rates: pd.DataFrame, export_rates: pd.DataFrame
) -> pd.DataFrame:
    joined = join_one_direction(half_hourly, import_rates, "import")
    joined = join_one_direction(joined, export_rates, "export")
    return joined


def join_one_direction(
    half_hourly: pd.DataFrame, rates: pd.DataFrame, direction: str
) -> pd.DataFrame:
    if rates.empty:
        frame = half_hourly.copy()
        frame[f"{direction}_rate_inc_vat"] = pd.NA
        frame[f"{direction}_tariff_code"] = pd.NA
        frame[f"{direction}_product_code"] = pd.NA
        return frame

    rates = select_deterministic_rates(rates)

    frame = half_hourly.sort_values("settlement_start_utc").copy()
    merged = pd.merge_asof(
        frame,
        rates,
        left_on="settlement_start_utc",
        right_on="valid_from",
        direction="backward",
    )
    covered = merged["valid_to"].isna() | (merged["valid_to"] >= merged["settlement_end_utc"])
    merged.loc[~covered, ["value_inc_vat", "tariff_code", "product_code"]] = pd.NA
    return merged.rename(
        columns={
            "value_inc_vat": f"{direction}_rate_inc_vat",
            "tariff_code": f"{direction}_tariff_code",
            "product_code": f"{direction}_product_code",
        }
    ).drop(
        columns=[
            column
            for column in [
                "agreement_id",
                "direction",
                "rate_type",
                "valid_from",
                "valid_to",
                "payment_method",
                "source_endpoint",
                "ingestion_timestamp",
            ]
            if column in merged.columns
        ]
    )


def prepare_active_rates(
    rates: pd.DataFrame, agreements: pd.DataFrame, direction: str
) -> pd.DataFrame:
    if rates.empty or agreements.empty:
        return rates

    active_agreements = agreements[agreements["direction"] == direction].copy()
    active_agreements["agreement_valid_from"] = pd.to_datetime(
        active_agreements["valid_from"], utc=True
    )
    active_agreements["agreement_valid_to"] = pd.to_datetime(
        active_agreements["valid_to"], utc=True
    )
    positive = active_agreements["agreement_valid_to"].isna() | (
        active_agreements["agreement_valid_to"] > active_agreements["agreement_valid_from"]
    )
    active_agreements = active_agreements[positive][
        ["agreement_id", "agreement_valid_from", "agreement_valid_to"]
    ]

    frame = rates.copy()
    frame["valid_from"] = pd.to_datetime(frame["valid_from"], utc=True)
    frame["valid_to"] = pd.to_datetime(frame["valid_to"], utc=True)
    frame = frame.merge(active_agreements, on="agreement_id", how="inner")
    if frame.empty:
        return frame

    frame["valid_from"] = frame[["valid_from", "agreement_valid_from"]].max(axis=1)
    frame["valid_to"] = frame.apply(clip_rate_end_to_agreement, axis=1)
    positive_rate = frame["valid_to"].isna() | (frame["valid_to"] > frame["valid_from"])
    return (
        frame[positive_rate]
        .drop(columns=["agreement_valid_from", "agreement_valid_to"])
        .reset_index(drop=True)
    )


def clip_rate_end_to_agreement(row: pd.Series) -> object:
    rate_end = row["valid_to"]
    agreement_end = row["agreement_valid_to"]
    if pd.isna(rate_end):
        return agreement_end
    if pd.isna(agreement_end):
        return rate_end
    return min(rate_end, agreement_end)


def select_deterministic_rates(rates: pd.DataFrame) -> pd.DataFrame:
    frame = rates.copy()
    frame["valid_from"] = pd.to_datetime(frame["valid_from"], utc=True)
    frame["valid_to"] = pd.to_datetime(frame["valid_to"], utc=True)
    frame["payment_priority"] = (
        frame["payment_method"]
        .map({"DIRECT_DEBIT": 0, "UNKNOWN": 1, "NON_DIRECT_DEBIT": 2})
        .fillna(1)
    )
    frame = frame.sort_values(
        ["valid_from", "valid_to", "payment_priority", "value_inc_vat", "tariff_code"]
    )
    return frame.drop_duplicates(["agreement_id", "valid_from", "valid_to"], keep="first").drop(
        columns=["payment_priority"]
    )


def calculate_financial_values(joined: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = joined.copy()
    if "export_agreement_status" not in frame:
        frame["export_agreement_status"] = "agreement_existed_rate_missing"
    frame["has_import_rate"] = frame["import_rate_inc_vat"].notna()
    frame["has_export_rate"] = frame["export_rate_inc_vat"].notna()
    frame["export_gap_reason"] = "covered"
    frame.loc[~frame["has_export_rate"], "export_gap_reason"] = frame.loc[
        ~frame["has_export_rate"], "export_agreement_status"
    ]
    frame["included_in_financials"] = frame["has_import_rate"] & frame["has_export_rate"]
    frame["avoided_import_value"] = 0.0
    frame["export_income"] = 0.0
    included = frame["included_in_financials"]
    frame.loc[included, "avoided_import_value"] = (
        frame.loc[included, "estimated_self_consumed_solar_kwh"]
        * frame.loc[included, "import_rate_inc_vat"]
        / 100
    )
    frame.loc[included, "export_income"] = (
        frame.loc[included, "export_kwh"] * frame.loc[included, "export_rate_inc_vat"] / 100
    )
    frame["total_financial_benefit"] = frame["avoided_import_value"] + frame["export_income"]
    frame["financial_status"] = "exact_tariff_rate_reconstructed_energy"
    frame.loc[~included, "financial_status"] = "excluded_missing_tariff_rate"

    quality_events = []
    missing_import = int((~frame["has_import_rate"]).sum())
    missing_export = int((~frame["has_export_rate"]).sum())
    missing_any = int((~frame["included_in_financials"]).sum())
    if missing_import:
        quality_events.append(
            event(
                "half_hours_without_import_rate", "error", missing_import, "Excluded from totals."
            )
        )
    if missing_export:
        quality_events.append(
            event(
                "half_hours_without_export_rate", "error", missing_export, "Excluded from totals."
            )
        )
        for reason, reason_frame in frame[~frame["has_export_rate"]].groupby("export_gap_reason"):
            quality_events.append(
                event(
                    f"missing_export_coverage_{reason}",
                    "error" if reason != "before_export_agreement" else "info",
                    len(reason_frame),
                    f"Export kWh excluded: {reason_frame['export_kwh'].sum():.6f}.",
                )
            )
    if missing_any:
        quality_events.append(
            event(
                "tariff_coverage_gap",
                "error",
                missing_any,
                "At least one required import/export rate missing; interval excluded.",
            )
        )
    quality_events.append(
        event(
            "export_statement_reconciliation_not_performed",
            "info",
            0,
            "Octopus statement reconciliation is not available from tariff-rate data alone.",
        )
    )
    return frame, pd.DataFrame(quality_events)


def annotate_export_agreement_status(
    half_hourly: pd.DataFrame, agreements: pd.DataFrame
) -> pd.DataFrame:
    frame = half_hourly.copy()
    frame["export_agreement_status"] = "before_export_agreement"
    active = active_agreements_for_direction(agreements, "export")
    if active.empty:
        return frame
    for agreement in active.itertuples(index=False):
        active_mask = frame["settlement_start_utc"] >= agreement.valid_from
        if pd.notna(agreement.valid_to):
            active_mask &= frame["settlement_start_utc"] < agreement.valid_to
        frame.loc[active_mask, "export_agreement_status"] = "agreement_existed_rate_missing"
    return frame


def active_agreements_for_direction(agreements: pd.DataFrame, direction: str) -> pd.DataFrame:
    active = agreements[agreements["direction"] == direction].copy()
    if active.empty:
        return active
    active["valid_from"] = pd.to_datetime(active["valid_from"], utc=True)
    active["valid_to"] = pd.to_datetime(active["valid_to"], utc=True)
    positive = active["valid_to"].isna() | (active["valid_to"] > active["valid_from"])
    return active[positive].copy()


def validate_rates(rates: pd.DataFrame, direction: str) -> pd.DataFrame:
    events: list[dict[str, object]] = []
    if rates.empty:
        return pd.DataFrame(
            [event(f"missing_{direction}_rates", "error", 0, "No rates available.")]
        )
    frame = rates.copy()
    frame["valid_from"] = pd.to_datetime(frame["valid_from"], utc=True)
    frame["valid_to"] = pd.to_datetime(frame["valid_to"], utc=True)
    duplicate_count = int(
        frame.duplicated(["tariff_code", "valid_from", "valid_to", "payment_method"]).sum()
    )
    if duplicate_count:
        events.append(
            event(
                f"duplicate_{direction}_rates", "warning", duplicate_count, "Duplicates retained."
            )
        )
    deterministic = select_deterministic_rates(frame)
    for tariff_code, group in deterministic.sort_values("valid_from").groupby("tariff_code"):
        previous_end = None
        for row in group.itertuples(index=False):
            if previous_end is not None and row.valid_from < previous_end:
                events.append(
                    event(
                        f"overlapping_{direction}_rates",
                        "error",
                        1,
                        f"Overlapping rates for tariff {tariff_code}.",
                    )
                )
            previous_end = row.valid_to
    return pd.DataFrame(events)


def validate_agreements(agreements: pd.DataFrame) -> pd.DataFrame:
    if agreements.empty:
        return pd.DataFrame(
            [event("missing_tariff_agreements", "error", 0, "No agreements found.")]
        )
    events: list[dict[str, object]] = []
    frame = agreements.copy()
    frame["valid_from"] = pd.to_datetime(frame["valid_from"], utc=True)
    frame["valid_to"] = pd.to_datetime(frame["valid_to"], utc=True)
    zero_duration = frame["valid_to"].notna() & (frame["valid_to"] <= frame["valid_from"])
    if zero_duration.any():
        events.append(
            event(
                "zero_duration_tariff_agreement",
                "info",
                int(zero_duration.sum()),
                "Agreement audit rows preserved but excluded from active tariff joins.",
            )
        )
    for key, group in frame.sort_values("valid_from").groupby(["meter_point_id", "direction"]):
        previous_end = None
        for row in group.itertuples(index=False):
            if previous_end is not None and row.valid_from < previous_end:
                events.append(
                    event(
                        "overlapping_tariff_agreements",
                        "error",
                        1,
                        f"Overlapping agreements for pseudonymous meter point {key}.",
                    )
                )
            previous_end = row.valid_to
    return pd.DataFrame(events)


def build_period_summary(frame: pd.DataFrame, freq: str) -> pd.DataFrame:
    included = frame[frame["included_in_financials"]].copy()
    if included.empty:
        return pd.DataFrame()
    included["period"] = (
        included["settlement_start_local"].dt.tz_localize(None).dt.to_period(freq).astype(str)
    )
    return (
        included.groupby("period", dropna=False)
        .agg(
            generation_kwh=("generation_kwh", "sum"),
            export_kwh=("export_kwh", "sum"),
            estimated_self_consumed_solar_kwh=("estimated_self_consumed_solar_kwh", "sum"),
            avoided_import_value=("avoided_import_value", "sum"),
            export_income=("export_income", "sum"),
            total_financial_benefit=("total_financial_benefit", "sum"),
            included_intervals=("included_in_financials", "sum"),
        )
        .reset_index()
    )


def build_lifetime_summary(frame: pd.DataFrame) -> FinancialSummary:
    total_intervals = len(frame)
    included = frame[frame["included_in_financials"]].copy()
    included_count = len(included)
    energy_coverage = 0.0 if total_intervals == 0 else included_count / total_intervals * 100
    tariff_coverage = energy_coverage
    status = (
        "exact_tariff_rate_reconstructed_energy" if included_count else "no_supported_intervals"
    )
    estimated_uncovered_export_value = estimate_uncovered_export_value(frame)
    confirmed_benefit = float(included["total_financial_benefit"].sum())
    return FinancialSummary(
        confirmed_avoided_import_value=round(float(included["avoided_import_value"].sum()), 2),
        confirmed_export_income=round(float(included["export_income"].sum()), 2),
        confirmed_financial_benefit=round(confirmed_benefit, 2),
        estimated_uncovered_export_value=round(estimated_uncovered_export_value, 2),
        estimated_lifetime_financial_benefit=round(
            confirmed_benefit + estimated_uncovered_export_value, 2
        ),
        energy_coverage_percentage=round(energy_coverage, 4),
        tariff_coverage_percentage=round(tariff_coverage, 4),
        excluded_intervals=total_intervals - included_count,
        avoided_import_status=status,
        export_income_status=status,
        total_financial_benefit_status=status,
        generated_at=datetime.now(UTC),
        period_start=None if frame.empty else frame["settlement_start_utc"].min().to_pydatetime(),
        period_end=None if frame.empty else frame["settlement_end_utc"].max().to_pydatetime(),
        notes=[
            "Standing charges, purchase costs, finance costs, maintenance, battery degradation and deemed export are excluded.",
            "Energy quantities are reconstructed from SolaX cumulative report intervals.",
            "No export income is estimated for dates before an export agreement existed.",
        ],
    )


def estimate_uncovered_export_value(frame: pd.DataFrame) -> float:
    eligible = frame[
        (~frame["has_export_rate"])
        & (frame["export_gap_reason"] == "agreement_existed_rate_missing")
    ]
    if eligible.empty:
        return 0.0
    covered = frame[frame["has_export_rate"]].copy()
    if covered.empty:
        return 0.0
    conservative_rate = covered["export_rate_inc_vat"].min()
    return float(eligible["export_kwh"].sum() * conservative_rate / 100)


def join_flags(values: pd.Series) -> str:
    flags = sorted(
        {flag for value in values.dropna().astype(str) for flag in value.split(";") if flag}
    )
    return ";".join(flags)


def event(event_type: str, severity: str, count: int, message: str) -> dict[str, object]:
    return {
        "event_type": event_type,
        "severity": severity,
        "count": count,
        "message": message,
    }


def write_parquet(frame: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, path, compression="snappy")


def main() -> int:
    summary = run_financial_pipeline()
    print(summary.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
