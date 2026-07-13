"""Transform cumulative SolaX daily readings into canonical intervals."""

from __future__ import annotations

import pandas as pd

from validation.solax_quality import ENERGY_COLUMNS, quality_event


def cumulative_to_intervals(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert cumulative daily counters into 5-minute interval values.

    Returns a wide canonical interval dataframe and a validation-event dataframe.
    """
    if raw_df.empty:
        return _empty_canonical(), pd.DataFrame()

    df = raw_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for source_col, output_col in ENERGY_COLUMNS.items():
        df[output_col] = pd.to_numeric(df[source_col], errors="coerce")
    df = df.drop(columns=list(ENERGY_COLUMNS))
    df = df.sort_values(["source_filename", "timestamp", "source_file_hash"]).reset_index(drop=True)

    events: list[dict[str, object]] = []
    duplicate_mask = df.duplicated(["source_filename", "timestamp"], keep="first")
    for _, row in df[duplicate_mask].iterrows():
        events.append(
            quality_event(
                "duplicate_interval",
                row["timestamp"],
                "Duplicate timestamp in the same source workbook.",
                source_filename=row["source_filename"],
            )
        )
    df = df[~duplicate_mask].copy()

    interval_frames: list[pd.DataFrame] = []
    for source_filename, source_df in df.groupby("source_filename", sort=True):
        source_df = source_df.sort_values("timestamp").copy()
        source_df["date"] = source_df["timestamp"].dt.date
        previous_date = source_df["date"].shift(1)
        is_first_for_day = source_df["date"] != previous_date
        interval_df = pd.DataFrame(
            {
                "interval_start": source_df["timestamp"].shift(1),
                "interval_end": source_df["timestamp"],
                "date": source_df["timestamp"].dt.date.astype(str),
                "source_filename": source_filename,
                "source_file_hash": source_df["source_file_hash"],
            }
        )
        interval_df.loc[is_first_for_day, "interval_start"] = source_df.loc[
            is_first_for_day, "timestamp"
        ].dt.normalize()

        quality_flags = pd.Series("", index=source_df.index, dtype="object")
        for output_col in ENERGY_COLUMNS.values():
            delta = source_df[output_col].diff()
            delta[is_first_for_day] = source_df.loc[is_first_for_day, output_col]

            midnight_reset_mask = is_first_for_day & source_df[output_col].notna()
            for _, row in source_df[
                midnight_reset_mask & (source_df["timestamp"].dt.time.astype(str) == "00:00:00")
            ].iterrows():
                events.append(
                    quality_event(
                        "midnight_reset",
                        row["timestamp"],
                        "Cumulative daily counter starts a new day.",
                        source_filename=source_filename,
                        field=output_col,
                        severity="info",
                    )
                )

            rollback_mask = (~is_first_for_day) & (delta < 0)
            for _, row in source_df[rollback_mask].iterrows():
                events.append(
                    quality_event(
                        "counter_rollback",
                        row["timestamp"],
                        "Cumulative counter decreased within a local day.",
                        source_filename=source_filename,
                        field=output_col,
                    )
                )
            delta[rollback_mask] = pd.NA
            interval_df[output_col] = delta.astype("Float64")
            quality_flags.loc[rollback_mask] = append_flag(
                quality_flags.loc[rollback_mask], f"{output_col}:counter_rollback"
            )

        interval_df["quality_flags"] = quality_flags.fillna("")
        zero_duration_mask = interval_df["interval_start"] >= interval_df["interval_end"]
        for _, row in interval_df[zero_duration_mask].iterrows():
            events.append(
                quality_event(
                    "zero_duration_daily_baseline",
                    row["interval_end"],
                    "First cumulative reading for the day has no positive-duration interval and is excluded.",
                    source_filename=source_filename,
                    severity="warning",
                )
            )
        interval_df = interval_df[~zero_duration_mask].copy()
        interval_frames.append(interval_df)

    canonical = (
        pd.concat(interval_frames, ignore_index=True) if interval_frames else _empty_canonical()
    )
    canonical = canonical[canonical["interval_end"].notna()].copy()
    canonical = canonical.sort_values(
        ["interval_start", "interval_end", "source_filename"]
    ).reset_index(drop=True)
    return canonical, pd.DataFrame(events)


def consolidate_canonical(canonical: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Collapse overlapping source rows into one row per interval."""
    if canonical.empty:
        return canonical, pd.DataFrame()

    events: list[dict[str, object]] = []
    key_cols = ["interval_start", "interval_end"]
    duplicate_mask = canonical.duplicated(key_cols, keep=False)
    for _, row in canonical[duplicate_mask].iterrows():
        events.append(
            quality_event(
                "overlapping_interval",
                row["interval_end"],
                "Interval appears in more than one workbook.",
                source_filename=row["source_filename"],
            )
        )

    value_cols = list(ENERGY_COLUMNS.values())

    def merge_group(group: pd.DataFrame) -> pd.Series:
        selected = group.sort_values("source_filename").iloc[0].copy()
        selected["source_filename"] = "|".join(
            sorted(group["source_filename"].astype(str).unique())
        )
        selected["source_file_hash"] = "|".join(
            sorted(group["source_file_hash"].astype(str).unique())
        )
        flags = [flag for flag in group["quality_flags"].astype(str) if flag]
        if len(group) > 1:
            flags.append("overlapping_interval")
        selected["quality_flags"] = "|".join(sorted(set("|".join(flags).split("|")) - {""}))
        for col in value_cols:
            selected[col] = group[col].dropna().iloc[0] if group[col].notna().any() else pd.NA
        return selected

    merged = (
        canonical.groupby(key_cols, sort=True, as_index=False, group_keys=False)
        .apply(merge_group)
        .reset_index(drop=True)
    )
    return merged.sort_values(key_cols).reset_index(drop=True), pd.DataFrame(events)


def append_flag(existing: pd.Series, flag: str) -> pd.Series:
    return existing.apply(lambda value: flag if not value else f"{value}|{flag}")


def _empty_canonical() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "interval_start",
            "interval_end",
            "date",
            "source_filename",
            "source_file_hash",
            *ENERGY_COLUMNS.values(),
            "quality_flags",
        ]
    )
