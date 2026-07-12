"""Validation report builders."""

from __future__ import annotations

import pandas as pd

from validation.solax_quality import ENERGY_COLUMNS


def build_daily_validation(canonical: pd.DataFrame, raw_df: pd.DataFrame) -> pd.DataFrame:
    if canonical.empty or raw_df.empty:
        return pd.DataFrame()

    raw = raw_df.copy()
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce")
    raw["date"] = raw["timestamp"].dt.date.astype(str)
    for source_col, output_col in ENERGY_COLUMNS.items():
        raw[output_col] = pd.to_numeric(raw[source_col], errors="coerce")

    final_rows = (
        raw.sort_values(["source_filename", "timestamp"])
        .groupby(["source_filename", "date"], as_index=False)
        .tail(1)
    )

    interval_sums = canonical.groupby(["source_filename", "date"], as_index=False)[
        list(ENERGY_COLUMNS.values())
    ].sum(min_count=1)

    records: list[dict[str, object]] = []
    for _, final in final_rows.iterrows():
        matches = interval_sums[
            (interval_sums["source_filename"] == final["source_filename"])
            & (interval_sums["date"] == final["date"])
        ]
        for field in ENERGY_COLUMNS.values():
            reconstructed = None if matches.empty else matches.iloc[0][field]
            final_value = final[field]
            difference = (
                None
                if pd.isna(reconstructed) or pd.isna(final_value)
                else float(reconstructed) - float(final_value)
            )
            records.append(
                {
                    "source_filename": final["source_filename"],
                    "date": final["date"],
                    "field": field,
                    "final_cumulative_kwh": final_value,
                    "reconstructed_interval_kwh": reconstructed,
                    "difference_kwh": difference,
                    "matches_final_cumulative": None
                    if difference is None
                    else abs(difference) <= 0.001,
                }
            )
    return (
        pd.DataFrame(records)
        .sort_values(["source_filename", "date", "field"])
        .reset_index(drop=True)
    )
