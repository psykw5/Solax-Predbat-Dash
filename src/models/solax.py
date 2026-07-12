"""Pydantic models for SolaX Plant Report ingestion."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class PlantReportMetadata(BaseModel):
    """Metadata extracted from a SolaX Plant Report workbook."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_path: Path
    source_filename: str
    source_file_hash: str
    sheet_name: str
    plant_name: str | None = None
    reporting_period_start: datetime | None = None
    reporting_period_end: datetime | None = None
    import_timestamp: datetime | None = None
    row_count: int = 0
    column_count: int = 0
    raw_metadata_hash: str | None = None


class IngestionSummary(BaseModel):
    """Deterministic summary of a pipeline run."""

    files_found: int
    files_processed: int
    files_failed: int
    raw_rows: int
    canonical_intervals: int
    validation_events: int
    output_files: list[str] = Field(default_factory=list)
