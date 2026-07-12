"""Filesystem helpers for deterministic ingestion."""

from __future__ import annotations

import hashlib
from pathlib import Path


def scan_xlsx_files(raw_dir: Path) -> list[Path]:
    """Recursively find SolaX Plant Report XLSX files in stable order."""
    if not raw_dir.exists():
        return []
    return sorted(
        path
        for path in raw_dir.rglob("*.xlsx")
        if path.is_file() and not path.name.startswith("~$")
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
