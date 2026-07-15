"""Load Wattson's central YAML configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from config.models import WattsonConfig

CONFIG_ENV_VAR = "WATTSON_CONFIG_PATH"
DEFAULT_CONFIG_PATH = Path("config/wattson.yaml")


class WattsonConfigError(ValueError):
    """Raised when the central Wattson configuration is missing or invalid."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    return repository_root() / DEFAULT_CONFIG_PATH


def configured_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return default_config_path()


def load_wattson_config(path: Path | str | None = None) -> WattsonConfig:
    config_path = Path(path).expanduser() if path is not None else configured_path()
    if not config_path.exists():
        raise WattsonConfigError(f"Wattson configuration file not found: {config_path}")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WattsonConfigError(f"Wattson configuration YAML is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise WattsonConfigError("Wattson configuration must be a YAML mapping.")
    try:
        return WattsonConfig.model_validate(payload)
    except ValidationError as exc:
        raise WattsonConfigError(f"Wattson configuration is invalid: {exc}") from exc


def load_wattson_config_dict(path: Path | str | None = None) -> dict[str, Any]:
    return load_wattson_config(path).model_dump(mode="json")
