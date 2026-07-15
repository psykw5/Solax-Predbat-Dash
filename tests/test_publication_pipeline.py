from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from live.publish import (
    WEBSITE_SUMMARY_RELATIVE_PATH,
    build_monthly_public_snapshot,
    load_monthly_public_snapshot,
    publish_monthly_summary,
    publish_public_snapshot,
    publish_website_snapshot,
    update_lock,
    validate_monthly_public_snapshot,
    validate_private_live_snapshot,
)
from live.store import LiveStore

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class PublicationPipelineTests(unittest.TestCase):
    def test_monthly_schema_rejects_live_and_daily_fields(self) -> None:
        snapshot = monthly_snapshot()
        snapshot["current_pv_power_kw"] = 1.2

        with self.assertRaises(ValueError):
            validate_monthly_public_snapshot(snapshot)

    def test_monthly_schema_rejects_exact_timestamps(self) -> None:
        snapshot = monthly_snapshot()
        snapshot["generated_at"] = "2026-07-14T12:34:56Z"

        with self.assertRaises(ValueError):
            validate_monthly_public_snapshot(snapshot)

    def test_monthly_loader_accepts_local_and_stable_website_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "wattson-monthly-summary.json"
            website = root / "wattson-summary.json"
            write_json(local, monthly_snapshot())
            write_json(website, monthly_snapshot())

            self.assertEqual(load_monthly_public_snapshot(local)["reporting_month"], "2026-05")
            self.assertEqual(load_monthly_public_snapshot(website)["reporting_month"], "2026-05")

    def test_previous_month_must_be_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            financial_dir = write_monthly_financial_inputs(Path(tmp), included_intervals=1440)
            energy_path = write_public_energy_inputs(Path(tmp))

            with self.assertRaises(ValueError):
                build_monthly_public_snapshot(
                    reporting_month="2026-07",
                    financial_dir=financial_dir,
                    energy_path=energy_path,
                    output_path=Path(tmp) / "wattson-monthly-summary.json",
                    today=date(2026, 7, 14),
                )

    def test_incomplete_monthly_data_blocks_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            financial_dir = write_monthly_financial_inputs(Path(tmp), included_intervals=100)
            energy_path = write_public_energy_inputs(Path(tmp))

            with self.assertRaises(ValueError):
                build_monthly_public_snapshot(
                    reporting_month="2026-06",
                    financial_dir=financial_dir,
                    energy_path=energy_path,
                    output_path=Path(tmp) / "wattson-monthly-summary.json",
                    today=date(2026, 7, 14),
                )

    def test_complete_monthly_snapshot_is_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "wattson-monthly-summary.json"
            financial_dir = write_monthly_financial_inputs(Path(tmp), included_intervals=1440)
            energy_path = write_public_energy_inputs(Path(tmp))

            snapshot = build_monthly_public_snapshot(
                reporting_month="2026-06",
                financial_dir=financial_dir,
                energy_path=energy_path,
                output_path=output,
                today=date(2026, 7, 14),
            )

            self.assertEqual(snapshot["reporting_month"], "2026-06")
            self.assertEqual(snapshot["publication_month"], "2026-07")
            self.assertNotIn("current_pv_power_kw", snapshot)
            self.assertEqual(snapshot["monthly_total_benefit_gbp"], 123.46)
            self.assertEqual(snapshot["lifetime_generation_kwh"], 1400.0)
            self.assertEqual(snapshot["lifetime_export_kwh"], 500.0)
            self.assertEqual(snapshot["lifetime_self_consumed_energy_kwh"], 900.0)
            self.assertEqual(snapshot["monthly_generation_kwh"], 400.0)
            self.assertNotIn("household_consumption_kwh", snapshot)
            self.assertNotIn("reported_inverter_consumption_kwh", snapshot)

    def test_public_energy_lifetime_uses_metrics_not_tariff_covered_financial_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "wattson-monthly-summary.json"
            financial_dir = write_monthly_financial_inputs(Path(tmp), included_intervals=1440)
            energy_path = write_public_energy_inputs(Path(tmp))

            snapshot = build_monthly_public_snapshot(
                reporting_month="2026-06",
                financial_dir=financial_dir,
                energy_path=energy_path,
                output_path=output,
                today=date(2026, 7, 14),
            )

            financial_months = read_csv(financial_dir / "monthly_financial_summary.csv")
            financial_generation = sum(float(row["generation_kwh"]) for row in financial_months)
            financial_export = sum(float(row["export_kwh"]) for row in financial_months)
            self.assertNotEqual(snapshot["lifetime_generation_kwh"], round(financial_generation, 1))
            self.assertNotEqual(snapshot["lifetime_export_kwh"], round(financial_export, 1))
            self.assertAlmostEqual(
                snapshot["lifetime_self_consumed_energy_kwh"] + snapshot["lifetime_export_kwh"],
                snapshot["lifetime_generation_kwh"],
                places=6,
            )

    def test_monthly_energy_values_are_subset_of_lifetime_energy_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "wattson-monthly-summary.json"
            financial_dir = write_monthly_financial_inputs(Path(tmp), included_intervals=1440)
            energy_path = write_public_energy_inputs(Path(tmp))

            snapshot = build_monthly_public_snapshot(
                reporting_month="2026-06",
                financial_dir=financial_dir,
                energy_path=energy_path,
                output_path=output,
                today=date(2026, 7, 14),
            )

            self.assertLessEqual(
                snapshot["monthly_generation_kwh"], snapshot["lifetime_generation_kwh"]
            )

    def test_monthly_publication_occurs_only_once_per_reporting_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = initialise_website_repo(root / "site")
            write_json(source, monthly_snapshot())
            write_json(website / WEBSITE_SUMMARY_RELATIVE_PATH, monthly_snapshot())

            result = publish_monthly_summary(source, website)

            self.assertEqual(result.status, "unchanged")
            self.assertEqual(result.material_changed_fields, [])

    def test_monthly_publication_replaces_older_same_month_stable_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = initialise_website_repo(root / "site")
            incoming = monthly_snapshot(reporting_month="2026-06")
            existing = dict(incoming)
            existing["lifetime_export_income_gbp"] = 12.34
            write_json(source, incoming)
            write_json(website / WEBSITE_SUMMARY_RELATIVE_PATH, existing)
            git(website, "add", WEBSITE_SUMMARY_RELATIVE_PATH.as_posix())
            git(website, "commit", "-m", "old summary")

            with (
                patch("live.publish.run_website_checks"),
                patch("live.publish.commit_and_push_website", return_value="commit_hash"),
            ):
                result = publish_monthly_summary(source, website)

            self.assertEqual(result.status, "published")
            self.assertIn("lifetime_export_income_gbp", result.material_changed_fields or [])
            self.assertNotIn(
                "lifetime_export_income_gbp",
                json.loads((website / WEBSITE_SUMMARY_RELATIVE_PATH).read_text(encoding="utf-8")),
            )

    def test_monthly_website_diff_stages_only_monthly_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = initialise_website_repo(root / "site")
            write_json(source, monthly_snapshot(reporting_month="2026-06"))

            with (
                patch("live.publish.run_website_checks"),
                patch("live.publish.commit_and_push_website", return_value="commit_hash"),
            ):
                result = publish_monthly_summary(source, website)

            self.assertEqual(result.status, "published")
            self.assertIn("reporting_month", result.material_changed_fields or [])
            changed = git_output(website, "diff", "--name-only", "HEAD")
            self.assertEqual(
                WEBSITE_SUMMARY_RELATIVE_PATH.as_posix(), "src/data/wattson-summary.json"
            )
            self.assertEqual(changed, WEBSITE_SUMMARY_RELATIVE_PATH.as_posix())

    def test_private_live_snapshot_validates_locally_without_website_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-live-summary.json"
            website = root / "site"
            snapshot = public_snapshot(generated_at="2026-07-13T12:00:00Z")
            write_json(source, snapshot)

            result = publish_public_snapshot(source, website, now=NOW)

            self.assertEqual(result.status, "validated")
            self.assertFalse((website / "src" / "data" / "wattson-live-summary.json").exists())

    def test_refuses_unexpected_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "private-export.json"
            write_json(source, public_snapshot())

            with self.assertRaises(ValueError):
                publish_public_snapshot(source, Path(tmp) / "site", now=NOW)

    def test_strict_schema_rejects_extra_fields(self) -> None:
        snapshot = public_snapshot()
        snapshot["source_filename"] = "raw.xlsx"

        with self.assertRaises(ValueError):
            validate_private_live_snapshot(snapshot)

    def test_privacy_validation_rejects_private_terms(self) -> None:
        snapshot = public_snapshot()
        snapshot["health_status"] = "contains account token"

        with self.assertRaises(ValueError):
            validate_private_live_snapshot(snapshot)

    def test_health_validation_rejects_non_ok_snapshot(self) -> None:
        snapshot = public_snapshot()
        snapshot["health_status"] = "degraded"

        with self.assertRaises(ValueError):
            validate_private_live_snapshot(snapshot)

    def test_freshness_validation_rejects_too_fresh_snapshot(self) -> None:
        snapshot = public_snapshot()
        snapshot["freshness_minutes"] = 5

        with self.assertRaises(ValueError):
            validate_private_live_snapshot(snapshot)

    def test_live_website_publication_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-live-summary.json"
            website = initialise_website_repo(root / "site")
            write_json(source, public_snapshot())

            with self.assertRaises(ValueError):
                publish_website_snapshot(source, website, now=NOW)

    def test_valid_monthly_publication_records_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = root / "site"
            store = LiveStore(root / "live.sqlite")
            website = initialise_website_repo(root / "site")
            write_json(source, monthly_snapshot(reporting_month="2026-06"))

            with (
                patch("live.publish.run_website_checks"),
                patch("live.publish.commit_and_push_website", return_value="commit_hash"),
            ):
                result = publish_monthly_summary(source, website, store=store)

            row = store.connection.execute("select * from publication_runs").fetchone()
            store.close()
            self.assertEqual(result.status, "published")
            self.assertEqual(row["website_commit_hash"], "commit_hash")
            self.assertEqual(row["status"], "published")

    def test_dirty_website_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = initialise_website_repo(root / "site")
            write_json(source, monthly_snapshot(reporting_month="2026-06"))
            (website / "dirty.txt").write_text("dirty", encoding="utf-8")

            with self.assertRaises(ValueError):
                publish_monthly_summary(source, website)

    def test_wrong_branch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = initialise_website_repo(root / "site")
            git(website, "checkout", "-b", "preview")
            write_json(source, monthly_snapshot(reporting_month="2026-06"))

            with self.assertRaises(ValueError):
                publish_monthly_summary(source, website)

    def test_unexpected_staged_file_blocks_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = initialise_website_repo(root / "site")
            write_json(source, monthly_snapshot(reporting_month="2026-06"))

            with (
                patch("live.publish.run_website_checks"),
                patch("live.publish.stage_only_path", side_effect=ValueError("unexpected")),
                self.assertRaises(ValueError),
            ):
                publish_monthly_summary(source, website)

    def test_website_check_failure_restores_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = initialise_website_repo(root / "site")
            existing = monthly_snapshot(reporting_month="2026-05")
            write_json(source, monthly_snapshot(reporting_month="2026-06"))
            write_json(website / WEBSITE_SUMMARY_RELATIVE_PATH, existing)

            with (
                patch("live.publish.run_website_checks", side_effect=ValueError("check failed")),
                self.assertRaises(ValueError),
            ):
                publish_monthly_summary(source, website)

            self.assertEqual(
                json.loads((website / WEBSITE_SUMMARY_RELATIVE_PATH).read_text(encoding="utf-8")),
                existing,
            )

    def test_push_failure_blocks_publication_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = initialise_website_repo(root / "site")
            store = LiveStore(root / "live.sqlite")
            write_json(source, monthly_snapshot(reporting_month="2026-06"))

            with (
                patch("live.publish.run_website_checks"),
                patch("live.publish.commit_and_push_website", side_effect=ValueError("push")),
                self.assertRaises(ValueError),
            ):
                publish_monthly_summary(source, website, store=store)

            rows = store.connection.execute("select count(*) from publication_runs").fetchone()[0]
            store.close()
            self.assertEqual(rows, 0)

    def test_overlapping_update_lock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "update.lock"
            with update_lock(lock_path), self.assertRaises(ValueError), update_lock(lock_path):
                pass

    def test_private_fields_never_reach_website_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wattson-monthly-summary.json"
            website = initialise_website_repo(root / "site")
            write_json(source, monthly_snapshot(reporting_month="2026-06"))

            with (
                patch("live.publish.run_website_checks"),
                patch("live.publish.commit_and_push_website", return_value="commit_hash"),
            ):
                publish_monthly_summary(source, website)

            published = (
                (website / WEBSITE_SUMMARY_RELATIVE_PATH).read_text(encoding="utf-8").lower()
            )
            for private in ["account", "mpan", "serial", "wifi", "token", "api_key"]:
                self.assertNotIn(private, published)


def public_snapshot(
    generated_at: str = "2026-07-13T12:00:00Z",
    data_as_of: str = "2026-07-13T10:30:00Z",
    freshness_minutes: int = 90,
) -> dict[str, object]:
    return {
        "confirmed_lifetime_financial_benefit_gbp": 4714.75,
        "current_battery_direction": "discharge",
        "current_battery_percentage": 96.0,
        "current_battery_power_kw": 0.29,
        "current_export_rate_p_per_kwh": 9.756,
        "current_grid_direction": "idle",
        "current_grid_power_kw": 0.0,
        "current_import_rate_p_per_kwh": 24.216,
        "current_pv_power_kw": 0.47,
        "data_as_of": data_as_of,
        "discounted_payback_month": "2035-05",
        "discounted_recovery_percentage": 33.0349,
        "freshness_minutes": freshness_minutes,
        "generated_at": generated_at,
        "health_status": "ok",
        "next_rate_p_per_kwh": 14.536,
        "next_tariff_change": "2026-07-14T01:00:00Z",
        "nominal_recovery_percentage": 36.2673,
        "simple_payback_month": "2032-04",
        "todays_generation_kwh": 38.6,
    }


def monthly_snapshot(reporting_month: str = "2026-05") -> dict[str, object]:
    return {
        "annual_summaries": [
            {
                "year": 2026,
                "generation_kwh": 1000.0,
                "avoided_import_value_gbp": 200.0,
                "export_income_gbp": 100.0,
                "total_benefit_gbp": 300.0,
            }
        ],
        "data_quality_status": "validated_monthly_financials",
        "discounted_payback_month": "2035-05",
        "discounted_recovery_percentage": 33.03,
        "lifetime_export_kwh": 700.0,
        "lifetime_financial_benefit_gbp": 4714.75,
        "lifetime_generation_kwh": 1400.0,
        "lifetime_self_consumed_energy_kwh": 700.0,
        "monthly_avoided_import_value_gbp": 80.12,
        "monthly_export_income_gbp": 43.34,
        "monthly_generation_kwh": 400.0,
        "monthly_total_benefit_gbp": 123.46,
        "nominal_recovery_percentage": 36.27,
        "publication_month": "2026-07",
        "reporting_month": reporting_month,
        "simple_payback_month": "2032-04",
    }


def write_monthly_financial_inputs(root: Path, included_intervals: int) -> Path:
    financial = root / "financial"
    financial.mkdir(parents=True, exist_ok=True)
    (financial / "monthly_financial_summary.csv").write_text(
        "\n".join(
            [
                "period,generation_kwh,export_kwh,estimated_self_consumed_solar_kwh,avoided_import_value,export_income,total_financial_benefit,included_intervals",
                f"2026-06,400.04,180.02,220.02,80.123,43.337,123.46,{included_intervals}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (financial / "annual_financial_summary.csv").write_text(
        "\n".join(
            [
                "period,generation_kwh,export_kwh,estimated_self_consumed_solar_kwh,avoided_import_value,export_income,total_financial_benefit,included_intervals",
                "2026,1000.0,450.0,550.0,200.0,100.0,300.0,1440",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        financial / "lifetime_summary.json",
        {
            "confirmed_financial_benefit": 4714.75,
            "total_financial_benefit_status": "exact_tariff_rate_reconstructed_energy",
        },
    )
    write_json(
        financial / "payback_public_summary.json",
        {
            "discounted_recovery_percentage": 33.0349,
            "nominal_recovery_percentage": 36.2673,
            "projected_discounted_payback_month": "2035-05",
            "projected_simple_payback_month": "2032-04",
        },
    )
    return financial


def write_public_energy_inputs(root: Path) -> Path:
    parquet = root / "solax_intervals.parquet"
    pd.DataFrame(
        [
            energy_interval("2026-05-01 00:00:00", "2026-05-01 00:05:00", 1000.0, 300.0, 9999.0),
            energy_interval("2026-06-01 00:00:00", "2026-06-01 00:05:00", 400.0, 200.0, 8888.0),
        ]
    ).to_parquet(parquet, index=False)
    return parquet


def energy_interval(
    start: str, end: str, generation: float, exported: float, reported_consumed: float
) -> dict[str, object]:
    return {
        "interval_start": pd.Timestamp(start),
        "interval_end": pd.Timestamp(end),
        "date": start[:10],
        "source_filename": "synthetic.xlsx",
        "source_file_hash": "hash",
        "pv_yield_kwh": generation,
        "inverter_output_kwh": generation,
        "exported_energy_kwh": exported,
        "consumed_energy_kwh": reported_consumed,
        "imported_energy_kwh": 0.0,
        "quality_flags": "",
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def initialise_website_repo(path: Path) -> Path:
    (path / "src" / "data").mkdir(parents=True, exist_ok=True)
    (path / "astro.config.mjs").write_text("export default {};\n", encoding="utf-8")
    (path / "package.json").write_text('{"scripts":{}}\n', encoding="utf-8")
    write_json(path / WEBSITE_SUMMARY_RELATIVE_PATH, monthly_snapshot(reporting_month="2026-05"))
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test.invalid")
    git(path, "config", "user.name", "Test User")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial")
    git(path, "remote", "add", "origin", "https://example.invalid/site.git")
    return path


def git(path: Path, *args: str) -> None:
    import subprocess

    result = subprocess.run(["git", *args], cwd=path, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def git_output(path: Path, *args: str) -> str:
    import subprocess

    result = subprocess.run(["git", *args], cwd=path, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
