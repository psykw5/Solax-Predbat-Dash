from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from config.loader import load_wattson_config


class WattsonConfigTests(unittest.TestCase):
    def test_successful_default_load(self) -> None:
        config = load_wattson_config()

        self.assertEqual(config.schema_version, 1)
        self.assertEqual(config.site.public_region, "Midlands, UK")
        self.assertEqual(config.solar.installed_capacity_kwp, 6.4)

    def test_capacity_reconciliation(self) -> None:
        payload = default_payload()
        payload["solar"]["installed_capacity_kwp"] = 6.5

        with tempfile_config(payload) as path, self.assertRaises(ValueError):
            load_wattson_config(path)

    def test_invalid_soc_ranges(self) -> None:
        payload = default_payload()
        payload["battery"]["minimum_soc_percent"] = 100

        with tempfile_config(payload) as path, self.assertRaises(ValueError):
            load_wattson_config(path)

    def test_unknown_keys_are_errors(self) -> None:
        payload = default_payload()
        payload["solar"]["unexpected"] = "nope"

        with tempfile_config(payload) as path, self.assertRaises(ValueError):
            load_wattson_config(path)

    def test_invalid_timezone(self) -> None:
        payload = default_payload()
        payload["site"]["timezone"] = "Europe/NotARealPlace"

        with tempfile_config(payload) as path, self.assertRaises(ValueError):
            load_wattson_config(path)

    def test_exact_coordinate_rejection(self) -> None:
        payload = default_payload()
        payload["site"]["latitude"] = 52.1

        with tempfile_config(payload) as path, self.assertRaises(ValueError):
            load_wattson_config(path)

    def test_path_override(self) -> None:
        payload = default_payload()
        payload["site"]["public_region"] = "Test Region"

        with (
            tempfile_config(payload) as path,
            patch.dict(os.environ, {"WATTSON_CONFIG_PATH": str(path)}),
        ):
            config = load_wattson_config()

        self.assertEqual(config.site.public_region, "Test Region")

    def test_null_optional_technical_values(self) -> None:
        config = load_wattson_config()

        self.assertIsNone(config.battery.charge_power_limit_kw)
        self.assertIsNone(config.battery.discharge_power_limit_kw)
        self.assertIsNone(config.battery.charge_efficiency)
        self.assertIsNone(config.battery.discharge_efficiency)
        self.assertIsNone(config.weather.pvgis.system_loss_percent)

    def test_public_live_data_is_prohibited(self) -> None:
        payload = default_payload()
        payload["publication"]["allow_live_household_data"] = True

        with tempfile_config(payload) as path, self.assertRaises(ValueError):
            load_wattson_config(path)

    def test_existing_cli_help_still_imports(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        commands = [
            [sys.executable, "-m", "src.live", "--help"],
            [sys.executable, "-m", "src.tariffs", "--help"],
        ]

        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)


def default_payload() -> dict[str, object]:
    return yaml.safe_load(Path("config/wattson.yaml").read_text(encoding="utf-8"))


class tempfile_config:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.directory: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "wattson.yaml"
        self.path.write_text(yaml.safe_dump(self.payload, sort_keys=False), encoding="utf-8")
        return self.path

    def __exit__(self, *_exc: object) -> None:
        if self.directory is not None:
            self.directory.cleanup()


if __name__ == "__main__":
    unittest.main()
