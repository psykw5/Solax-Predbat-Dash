"""Validation helpers for SolaX interval data."""

from __future__ import annotations

import pandas as pd

EXPECTED_INTERVAL = pd.Timedelta(minutes=5)
ENERGY_COLUMNS = {
    "Daily PV Yield(kWh)": "pv_yield_kwh",
    "Daily inverter output (kWh)": "inverter_output_kwh",
    "Daily exported energy(kWh)": "exported_energy_kwh",
    "Daily consumed(kWh)": "consumed_energy_kwh",
    "Daily imported energy(kWh)": "imported_energy_kwh",
}


def quality_event(
    event_type: str,
    timestamp: pd.Timestamp | None,
    message: str,
    source_filename: str | None = None,
    field: str | None = None,
    severity: str = "warning",
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "severity": severity,
        "source_filename": source_filename,
        "timestamp": pd.NaT if timestamp is None else timestamp,
        "field": field,
        "message": message,
    }


def detect_missing_timestamps(df: pd.DataFrame) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    if df.empty:
        return events
    for source_filename, source_df in df.groupby("source_filename", sort=True):
        timestamps = (
            source_df["timestamp"].dropna().drop_duplicates().sort_values().reset_index(drop=True)
        )
        for previous, current in zip(timestamps.iloc[:-1], timestamps.iloc[1:], strict=False):
            if previous.date() != current.date():
                continue
            expected = previous + EXPECTED_INTERVAL
            while expected < current:
                events.append(
                    quality_event(
                        "missing_timestamp",
                        expected,
                        "Expected five-minute timestamp is absent.",
                        source_filename=source_filename,
                    )
                )
                expected += EXPECTED_INTERVAL
    return events


def detect_dst_transitions(
    df: pd.DataFrame, timezone_name: str = "Europe/London"
) -> list[dict[str, object]]:
    """Flag local timestamps that are ambiguous/nonexistent in the site timezone."""
    events: list[dict[str, object]] = []
    if df.empty:
        return events
    for source_filename, source_df in df.groupby("source_filename", sort=True):
        for timestamp in source_df["timestamp"].dropna().drop_duplicates().sort_values():
            probe = pd.DatetimeIndex([timestamp])
            try:
                probe.tz_localize(timezone_name, ambiguous="raise", nonexistent="raise")
            except Exception as exc:
                events.append(
                    quality_event(
                        "daylight_saving_transition",
                        timestamp,
                        f"Timestamp is ambiguous or nonexistent in {timezone_name}: {type(exc).__name__}.",
                        source_filename=source_filename,
                    )
                )
    return events


def detect_overlapping_reporting_periods(periods: pd.DataFrame) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    if periods.empty:
        return events
    ordered = periods.sort_values(
        ["reporting_period_start", "reporting_period_end", "source_filename"]
    )
    previous = None
    for _, row in ordered.iterrows():
        if (
            previous is not None
            and row["reporting_period_start"] <= previous["reporting_period_end"]
        ):
            events.append(
                quality_event(
                    "overlapping_reporting_period",
                    row["reporting_period_start"],
                    "Reporting period overlaps with another workbook.",
                    source_filename=row["source_filename"],
                )
            )
        previous = row
    return events
