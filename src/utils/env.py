"""Small .env reader for local-only credentials."""

from __future__ import annotations

from pathlib import Path


def read_dotenv(path: Path = Path(".env")) -> dict[str, str]:
    """Read KEY=VALUE lines without logging or exporting secrets."""
    if not path.exists():
        raise FileNotFoundError(f"Local .env file not found: {path}")

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def require_env_values(keys: list[str], path: Path = Path(".env")) -> dict[str, str]:
    values = read_dotenv(path)
    missing = [key for key in keys if not values.get(key)]
    if missing:
        raise ValueError(f"Missing required .env keys: {', '.join(missing)}")
    return {key: values[key] for key in keys}
