"""Safe reader for SolaX Plant Report XLSX workbooks."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from models.solax import PlantReportMetadata
from utils.files import sha256_file
from utils.redaction import redact_sensitive_text, text_hash

HEADER_ROW_INDEX = 1
DATA_START_ROW_INDEX = 2
TIMESTAMP_COLUMN = "Update time"

REQUIRED_COLUMNS = [
    "Update time",
    "Daily PV Yield(kWh)",
    "Daily inverter output (kWh)",
    "Daily exported energy(kWh)",
    "Daily consumed(kWh)",
    "Daily imported energy(kWh)",
]

FALLBACK_ORDERED_COLUMNS = ["No.", *REQUIRED_COLUMNS]


def read_plant_report(path: Path) -> tuple[PlantReportMetadata, pd.DataFrame]:
    """Read one workbook and return metadata plus raw cumulative rows."""
    with pd.ExcelFile(path) as workbook:
        sheet_name = workbook.sheet_names[0]
    raw = pd.read_excel(
        path,
        sheet_name=sheet_name,
        header=None,
        engine="openpyxl",
    )
    raw_metadata = str(raw.iloc[0, 0]) if not raw.empty else ""
    df = normalize_report_table(raw)
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"missing required SolaX report columns: {missing}")

    df = df[REQUIRED_COLUMNS].copy()
    df["timestamp"] = pd.to_datetime(df[TIMESTAMP_COLUMN], errors="coerce")
    for col in REQUIRED_COLUMNS:
        if col != TIMESTAMP_COLUMN:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.drop(columns=[TIMESTAMP_COLUMN])
    df.insert(0, "source_filename", path.name)
    df.insert(1, "source_file_hash", sha256_file(path))

    valid_timestamps = df["timestamp"].dropna()
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).replace(tzinfo=None)
    metadata = PlantReportMetadata(
        source_path=path,
        source_filename=path.name,
        source_file_hash=sha256_file(path),
        sheet_name=sheet_name,
        plant_name=redact_sensitive_text(parse_plant_name(raw_metadata)),
        reporting_period_start=valid_timestamps.min().to_pydatetime()
        if not valid_timestamps.empty
        else None,
        reporting_period_end=valid_timestamps.max().to_pydatetime()
        if not valid_timestamps.empty
        else None,
        import_timestamp=mtime,
        row_count=len(df),
        column_count=len(REQUIRED_COLUMNS),
        raw_metadata_hash=text_hash(raw_metadata),
    )
    return metadata, df


def normalize_report_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Return a named Plant Report table from standard or blank-header exports."""
    if raw.empty or len(raw) <= DATA_START_ROW_INDEX:
        raise ValueError("workbook does not contain enough rows for a Plant Report")

    header_values = [
        "" if pd.isna(value) else str(value).strip()
        for value in raw.iloc[HEADER_ROW_INDEX].tolist()
    ]
    if all(header in header_values for header in REQUIRED_COLUMNS):
        df = raw.iloc[DATA_START_ROW_INDEX:].copy()
        df.columns = header_values
        return df.dropna(how="all")

    if is_blank_header_known_layout(raw):
        df = raw.iloc[DATA_START_ROW_INDEX:, : len(FALLBACK_ORDERED_COLUMNS)].copy()
        df.columns = FALLBACK_ORDERED_COLUMNS
        return df.dropna(how="all")

    df = raw.iloc[DATA_START_ROW_INDEX:].copy()
    df.columns = header_values
    return df.dropna(how="all")


def is_blank_header_known_layout(raw: pd.DataFrame) -> bool:
    """Detect SolaX exports where the header row is blank but column order is intact."""
    if raw.shape[1] < len(FALLBACK_ORDERED_COLUMNS):
        return False
    header_values = raw.iloc[HEADER_ROW_INDEX, : len(FALLBACK_ORDERED_COLUMNS)].tolist()
    if not all(pd.isna(value) or str(value).strip() == "" for value in header_values):
        return False
    sample = raw.iloc[DATA_START_ROW_INDEX:, 1].dropna().head(5)
    if sample.empty:
        return False
    parsed = pd.to_datetime(sample, errors="coerce")
    return bool(parsed.notna().all())


def parse_plant_name(raw_metadata: str) -> str | None:
    """Best-effort plant name extraction from the report title row."""
    text = " ".join(str(raw_metadata or "").split())
    if not text:
        return None
    without_report = re.sub(r"plant\s*reports?", "", text, flags=re.IGNORECASE).strip()
    without_dates = re.sub(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}.*$", "", without_report).strip()
    return without_dates or text
