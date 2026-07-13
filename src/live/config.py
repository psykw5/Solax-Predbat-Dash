"""Configuration helpers for read-only live collection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from utils.env import read_dotenv

DEFAULT_LIVE_DIR = Path("data/live")
DEFAULT_RAW_LIVE_DIR = Path("data/raw/live")
DEFAULT_PUBLIC_DIR = Path("data/public")
DEFAULT_DB_PATH = DEFAULT_LIVE_DIR / "wattson-live.sqlite"
DEFAULT_PUBLIC_SNAPSHOT_PATH = DEFAULT_PUBLIC_DIR / "wattson-live-summary.json"
DEFAULT_PUBLIC_DATA_DELAY_MINUTES = 30

SOLAX_KEYS = ["SOLAX_TOKEN_ID", "SOLAX_WIFI_SN"]
OCTOPUS_KEYS = ["OCTOPUS_API_KEY", "OCTOPUS_ACCOUNT_NUMBER"]


def load_environment(path: Path = Path(".env")) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        values.update(read_dotenv(path))
    values.update({key: value for key, value in os.environ.items() if value})
    return values


def require_credentials(keys: list[str], path: Path = Path(".env")) -> dict[str, str]:
    values = load_environment(path)
    missing = [key for key in keys if not values.get(key)]
    if missing:
        raise ValueError(f"Missing required environment values: {', '.join(missing)}")
    return {key: values[key] for key in keys}


def public_delay_minutes(path: Path = Path(".env")) -> int:
    values = load_environment(path)
    raw_value = values.get("PUBLIC_DATA_DELAY_MINUTES", str(DEFAULT_PUBLIC_DATA_DELAY_MINUTES))
    delay = int(raw_value)
    if delay < 0:
        raise ValueError("PUBLIC_DATA_DELAY_MINUTES must not be negative.")
    return delay


def assert_private_outputs_ignored() -> None:
    paths = [
        ".env",
        "data/raw/live/example.json",
        "data/raw/octopus/example.json",
        "data/processed/octopus/example.parquet",
        "data/processed/financial/example.parquet",
        "data/live/wattson-live.sqlite",
        "data/live/wattson-live.sqlite-wal",
        "data/public/wattson-live-summary.json",
    ]
    result = subprocess.run(
        ["git", "-c", f"safe.directory={Path.cwd().as_posix()}", "check-ignore", *paths],
        check=False,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Required private live data paths are not ignored by Git.")
