"""Production-shaped SolaX Plant Report ETL pipeline."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ingestion.solax_reader import read_plant_report
from models.solax import IngestionSummary
from transforms.solax_intervals import consolidate_canonical, cumulative_to_intervals
from utils.files import ensure_output_dir, scan_xlsx_files
from utils.logging import configure_logging
from validation.reports import build_daily_validation
from validation.solax_quality import (
    detect_dst_transitions,
    detect_missing_timestamps,
    detect_overlapping_reporting_periods,
    quality_event,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_RAW_DIR = Path("data/raw/solax")
DEFAULT_PROCESSED_DIR = Path("data/processed/solax")


def run_pipeline(
    raw_dir: Path = DEFAULT_RAW_DIR, processed_dir: Path = DEFAULT_PROCESSED_DIR
) -> IngestionSummary:
    ensure_output_dir(processed_dir)
    files = scan_xlsx_files(raw_dir)
    metadata_records: list[dict[str, object]] = []
    raw_frames: list[pd.DataFrame] = []
    file_errors: list[dict[str, object]] = []

    for path in files:
        try:
            metadata, frame = read_plant_report(path)
        except Exception as exc:
            LOGGER.exception("Failed to read SolaX workbook")
            file_errors.append(
                quality_event(
                    "file_read_error",
                    None,
                    f"{type(exc).__name__}: unable to read workbook.",
                    source_filename=path.name,
                    severity="error",
                )
            )
            continue
        metadata_records.append(metadata.model_dump(mode="json", exclude={"source_path"}))
        raw_frames.append(frame)

    raw_df = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()
    metadata_df = (
        pd.DataFrame(metadata_records).sort_values("source_filename")
        if metadata_records
        else pd.DataFrame()
    )

    validation_events: list[pd.DataFrame] = []
    if file_errors:
        validation_events.append(pd.DataFrame(file_errors))
    if not raw_df.empty:
        validation_events.append(pd.DataFrame(detect_missing_timestamps(raw_df)))
        validation_events.append(pd.DataFrame(detect_dst_transitions(raw_df)))
    if not metadata_df.empty:
        validation_events.append(pd.DataFrame(detect_overlapping_reporting_periods(metadata_df)))

    canonical_by_source, transform_events = cumulative_to_intervals(raw_df)
    consolidated, overlap_events = consolidate_canonical(canonical_by_source)
    validation_events.extend([transform_events, overlap_events])
    validation_report = (
        pd.concat([frame for frame in validation_events if not frame.empty], ignore_index=True)
        if validation_events
        else pd.DataFrame(
            columns=["event_type", "severity", "source_filename", "timestamp", "field", "message"]
        )
    )

    daily_validation = build_daily_validation(canonical_by_source, raw_df)
    ingestion_summary = IngestionSummary(
        files_found=len(files),
        files_processed=len(metadata_records),
        files_failed=len(file_errors),
        raw_rows=len(raw_df),
        canonical_intervals=len(consolidated),
        validation_events=len(validation_report),
        output_files=[
            "solax_intervals.parquet",
            "solax_intervals.csv",
            "validation_report.csv",
            "daily_validation_report.csv",
            "ingestion_summary.json",
            "report_metadata.csv",
        ],
    )

    write_outputs(
        processed_dir,
        consolidated,
        validation_report,
        daily_validation,
        metadata_df,
        ingestion_summary,
    )
    return ingestion_summary


def write_outputs(
    processed_dir: Path,
    canonical: pd.DataFrame,
    validation_report: pd.DataFrame,
    daily_validation: pd.DataFrame,
    metadata: pd.DataFrame,
    summary: IngestionSummary,
) -> None:
    canonical = canonical.sort_values(["interval_start", "interval_end"]).reset_index(drop=True)
    canonical.to_csv(processed_dir / "solax_intervals.csv", index=False)

    table = pa.Table.from_pandas(canonical, preserve_index=False)
    pq.write_table(table, processed_dir / "solax_intervals.parquet", compression="snappy")

    validation_report.sort_values(
        ["event_type", "source_filename", "timestamp", "field"], na_position="last"
    ).to_csv(processed_dir / "validation_report.csv", index=False)
    daily_validation.to_csv(processed_dir / "daily_validation_report.csv", index=False)
    metadata.to_csv(processed_dir / "report_metadata.csv", index=False)
    (processed_dir / "ingestion_summary.json").write_text(
        json.dumps(summary.model_dump(), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest SolaX Plant Report XLSX files.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    args = parser.parse_args()

    configure_logging()
    summary = run_pipeline(args.raw_dir, args.processed_dir)
    print(json.dumps(summary.model_dump(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
