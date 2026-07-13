from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from live.config import public_delay_minutes
from live.models import SolaXObservation, TariffSnapshot
from live.octopus import refresh_octopus_tariffs
from live.snapshot import build_public_snapshot
from live.solax import normalize_solax_payload
from live.store import LiveStore

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class LivePipelineTests(unittest.TestCase):
    def test_successful_solax_normalisation(self) -> None:
        payload = {
            "success": True,
            "code": 0,
            "exception": "Query success!",
            "result": {
                "utcDateTime": "2026-07-13T11:45:00Z",
                "acpower": 1234,
                "soc": 87,
                "batPower": -500,
                "feedinpower": 250,
                "yieldtoday": 18.6,
                "yieldtotal": 12000.5,
            },
        }

        observation = normalize_solax_payload(payload, NOW)

        self.assertEqual(observation.pv_power_kw, 1.234)
        self.assertEqual(observation.battery_direction, "discharge")
        self.assertEqual(observation.battery_power_kw, 0.5)
        self.assertEqual(observation.grid_direction, "export")
        self.assertEqual(observation.daily_generation_kwh, 18.6)

    def test_stale_solax_response_is_rejected(self) -> None:
        payload = {"result": {"uploadTime": "2026-07-13 09:00:00"}}

        with self.assertRaises(ValueError):
            normalize_solax_payload(payload, NOW)

    def test_malformed_solax_response_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_solax_payload({"success": False, "errorCode": 401}, NOW)

    def test_duplicate_observations_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LiveStore(Path(tmp) / "live.sqlite")
            observation = SolaXObservation(
                observation_timestamp=NOW - timedelta(minutes=45),
                received_at=NOW,
                pv_power_kw=1.2,
            )

            self.assertTrue(store.insert_solax_observation(observation))
            self.assertFalse(store.insert_solax_observation(observation))
            store.close()

    def test_octopus_agreement_change_triggers_backfill_warning(self) -> None:
        payload = account_payload("E-1R-NEW-IMPORT-A", "E-1R-NEW-EXPORT-A")
        client = FakeOctopusClient(payload)
        with (
            patch(
                "live.octopus.require_credentials",
                return_value={"OCTOPUS_API_KEY": "x", "OCTOPUS_ACCOUNT_NUMBER": "A"},
            ),
            patch("live.octopus.OctopusClient", return_value=client),
            tempfile.TemporaryDirectory() as tmp,
        ):
            snapshots, full_backfill = refresh_octopus_tariffs(
                previous_products={"import": "OLD-IMPORT"},
                raw_dir=Path(tmp),
                now=NOW,
            )

        self.assertTrue(full_backfill)
        self.assertEqual({snapshot.direction for snapshot in snapshots}, {"import", "export"})

    def test_incremental_tariff_refresh_without_product_change(self) -> None:
        payload = account_payload("E-1R-NEW-IMPORT-A", "E-1R-NEW-EXPORT-A")
        client = FakeOctopusClient(payload)
        with (
            patch(
                "live.octopus.require_credentials",
                return_value={"OCTOPUS_API_KEY": "x", "OCTOPUS_ACCOUNT_NUMBER": "A"},
            ),
            patch("live.octopus.OctopusClient", return_value=client),
            tempfile.TemporaryDirectory() as tmp,
        ):
            snapshots, full_backfill = refresh_octopus_tariffs(
                previous_products={"import": "NEW-IMPORT", "export": "NEW-EXPORT"},
                raw_dir=Path(tmp),
                now=NOW,
            )

        self.assertFalse(full_backfill)
        self.assertEqual(len(snapshots), 2)

    def test_public_delay_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            os.chdir(tmp)
            try:
                store = LiveStore(Path("live.sqlite"))
                store.insert_solax_observation(
                    SolaXObservation(
                        observation_timestamp=NOW - timedelta(minutes=10),
                        received_at=NOW,
                        pv_power_kw=9.9,
                    )
                )
                store.insert_solax_observation(
                    SolaXObservation(
                        observation_timestamp=NOW - timedelta(minutes=45),
                        received_at=NOW,
                        pv_power_kw=1.1,
                    )
                )
                insert_tariffs(store)
                write_summaries(Path("."))

                snapshot = build_public_snapshot(
                    store,
                    output_path=Path("public.json"),
                    delay_minutes=30,
                    now=NOW,
                )
            finally:
                os.chdir(cwd)
                store.close()

        self.assertEqual(snapshot.data_as_of, "2026-07-13T11:15:00Z")
        self.assertEqual(snapshot.current_pv_power_kw, 1.1)

    def test_public_snapshot_contains_no_private_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            os.chdir(tmp)
            try:
                store = LiveStore(Path("live.sqlite"))
                store.insert_solax_observation(
                    SolaXObservation(
                        observation_timestamp=NOW - timedelta(minutes=45),
                        received_at=NOW,
                        pv_power_kw=1.234,
                        battery_soc_percent=70,
                        battery_power_kw=0.4,
                        battery_direction="charge",
                        grid_power_kw=0.2,
                        grid_direction="import",
                        daily_generation_kwh=12.34,
                    )
                )
                insert_tariffs(store)
                write_summaries(Path("."))
                snapshot = build_public_snapshot(store, Path("public.json"), 30, NOW)
            finally:
                os.chdir(cwd)
                store.close()

        text = snapshot.model_dump_json()
        for forbidden in ["account", "mpan", "serial", "wifi", "token", "C:\\"]:
            self.assertNotIn(forbidden, text.lower())

    def test_snapshot_generation_with_partial_source_failure_uses_previous_valid_observation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            os.chdir(tmp)
            try:
                store = LiveStore(Path("live.sqlite"))
                store.insert_solax_observation(
                    SolaXObservation(
                        observation_timestamp=NOW - timedelta(minutes=35),
                        received_at=NOW - timedelta(minutes=34),
                        pv_power_kw=2.0,
                    )
                )
                insert_tariffs(store)
                write_summaries(Path("."))
                snapshot = build_public_snapshot(store, Path("public.json"), 30, NOW)
            finally:
                os.chdir(cwd)
                store.close()

        self.assertEqual(snapshot.health_status, "ok")
        self.assertEqual(snapshot.current_pv_power_kw, 2.0)

    def test_no_publication_when_mandatory_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LiveStore(Path(tmp) / "live.sqlite")
            with self.assertRaises(ValueError):
                build_public_snapshot(store, Path(tmp) / "public.json", 30, NOW)
            self.assertFalse((Path(tmp) / "public.json").exists())
            store.close()

    def test_negative_public_delay_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("PUBLIC_DATA_DELAY_MINUTES=-1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                public_delay_minutes(path)


class FakeOctopusClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def account(self, account_number: str) -> tuple[str, dict[str, object]]:
        return "https://api.octopus.energy/v1/accounts/redacted/", self.payload

    def paged(self, url: str) -> list[dict[str, object]]:
        direction = "export" if "EXPORT" in url else "import"
        value = 15.0 if direction == "export" else 30.0
        return [
            {
                "value_inc_vat": value,
                "valid_from": "2026-07-13T00:00:00Z",
                "valid_to": "2026-07-13T12:30:00Z",
            },
            {
                "value_inc_vat": value + 1,
                "valid_from": "2026-07-13T12:30:00Z",
                "valid_to": "2026-07-14T00:00:00Z",
            },
        ]


def account_payload(import_tariff: str, export_tariff: str) -> dict[str, object]:
    return {
        "properties": [
            {
                "electricity_meter_points": [
                    {
                        "mpan": "1111111111111",
                        "is_export": False,
                        "agreements": [
                            {
                                "tariff_code": import_tariff,
                                "valid_from": "2026-01-01T00:00:00Z",
                                "valid_to": None,
                            }
                        ],
                    },
                    {
                        "mpan": "2222222222222",
                        "is_export": True,
                        "agreements": [
                            {
                                "tariff_code": export_tariff,
                                "valid_from": "2026-01-01T00:00:00Z",
                                "valid_to": None,
                            }
                        ],
                    },
                ]
            }
        ]
    }


def insert_tariffs(store: LiveStore) -> None:
    store.insert_tariff_snapshots(
        [
            TariffSnapshot(
                direction="import",
                tariff_code="IMPORT",
                product_code="IMPORT",
                rate_inc_vat=30.1234,
                valid_from=NOW - timedelta(hours=1),
                next_rate_inc_vat=31.0,
                next_valid_from=NOW + timedelta(hours=1),
                captured_at=NOW,
            ),
            TariffSnapshot(
                direction="export",
                tariff_code="EXPORT",
                product_code="EXPORT",
                rate_inc_vat=15.5678,
                valid_from=NOW - timedelta(hours=1),
                next_rate_inc_vat=16.0,
                next_valid_from=NOW + timedelta(hours=2),
                captured_at=NOW,
            ),
        ]
    )


def write_summaries(root: Path) -> None:
    financial = root / "data" / "processed" / "financial"
    financial.mkdir(parents=True, exist_ok=True)
    (financial / "lifetime_summary.json").write_text(
        json.dumps({"confirmed_financial_benefit": 4714.75}), encoding="utf-8"
    )
    (financial / "payback_public_summary.json").write_text(
        json.dumps(
            {
                "nominal_recovery_percentage": 36.2673,
                "discounted_recovery_percentage": 33.0349,
                "projected_simple_payback_month": "2032-04",
                "projected_discounted_payback_month": "2035-05",
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
