"""CLI for read-only Wattson live collection."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from live.config import assert_private_outputs_ignored, public_delay_minutes
from live.models import CollectorRun, QualityEvent
from live.octopus import refresh_octopus_tariffs
from live.publish import (
    build_monthly_public_snapshot,
    publish_monthly_summary,
    publish_public_snapshot,
    update_lock,
)
from live.snapshot import build_public_snapshot
from live.solax import collect_solax_observation
from live.store import LiveStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Wattson live read-only collection.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect-solax")
    subparsers.add_parser("refresh-octopus")
    subparsers.add_parser("build-public-snapshot")
    subparsers.add_parser("publish-public-snapshot")
    subparsers.add_parser("publish-monthly-summary")
    subparsers.add_parser("publish-website")
    subparsers.add_parser("update-public-dashboard")
    subparsers.add_parser("run")
    args = parser.parse_args()

    assert_private_outputs_ignored()
    store = LiveStore()
    try:
        if args.command == "collect-solax":
            return command_collect_solax(store)
        if args.command == "refresh-octopus":
            return command_refresh_octopus(store)
        if args.command == "build-public-snapshot":
            return command_build_public_snapshot(store)
        if args.command == "publish-public-snapshot":
            return command_publish_public_snapshot()
        if args.command == "publish-monthly-summary":
            return command_publish_monthly_summary(store)
        if args.command == "publish-website":
            return command_publish_website(store)
        if args.command == "update-public-dashboard":
            return command_update_public_dashboard(store)
        return command_run(store)
    finally:
        store.close()


def command_collect_solax(store: LiveStore) -> int:
    started = datetime.now(UTC)
    try:
        observation = collect_solax_observation(now=started)
        inserted = store.insert_solax_observation(observation)
        message = "inserted" if inserted else "duplicate"
        status = "success"
    except Exception as exc:
        store.insert_quality_event(
            QualityEvent(
                event_type="solax_collection_failed",
                severity="error",
                message=type(exc).__name__,
                observed_at=started,
            )
        )
        message = type(exc).__name__
        status = "failed"
    store.insert_collector_run(run("solax", started, status, message))
    print(json.dumps({"collector": "solax", "status": status, "message": message}))
    return 0 if status == "success" else 1


def command_refresh_octopus(store: LiveStore) -> int:
    started = datetime.now(UTC)
    try:
        snapshots, full_backfill_required = refresh_octopus_tariffs(
            previous_products=store.previous_products(),
            now=started,
        )
        store.insert_tariff_snapshots(snapshots)
        if full_backfill_required:
            store.insert_quality_event(
                QualityEvent(
                    event_type="octopus_product_changed_full_backfill_required",
                    severity="warning",
                    message="Active product code changed; run the historical tariff backfill.",
                    observed_at=started,
                )
            )
        status = "success"
        message = f"{len(snapshots)} active tariff snapshots"
    except Exception as exc:
        store.insert_quality_event(
            QualityEvent(
                event_type="octopus_refresh_failed",
                severity="error",
                message=type(exc).__name__,
                observed_at=started,
            )
        )
        status = "failed"
        message = type(exc).__name__
    store.insert_collector_run(run("octopus", started, status, message))
    print(json.dumps({"collector": "octopus", "status": status, "message": message}))
    return 0 if status == "success" else 1


def command_build_public_snapshot(store: LiveStore) -> int:
    started = datetime.now(UTC)
    try:
        snapshot = build_public_snapshot(store, delay_minutes=public_delay_minutes(), now=started)
        status = "success"
        message = "published"
        print(snapshot.model_dump_json(indent=2))
    except Exception as exc:
        status = "failed"
        message = type(exc).__name__
        store.insert_quality_event(
            QualityEvent(
                event_type="public_snapshot_failed",
                severity="error",
                message=message,
                observed_at=started,
            )
        )
        print(json.dumps({"collector": "public_snapshot", "status": status, "message": message}))
    store.insert_collector_run(run("public_snapshot", started, status, message))
    return 0 if status == "success" else 1


def command_run(store: LiveStore) -> int:
    solax_status = command_collect_solax(store)
    octopus_status = 0
    last_octopus = store.last_successful_run("octopus")
    if last_octopus is None or datetime.now(UTC) - last_octopus >= timedelta(hours=24):
        octopus_status = command_refresh_octopus(store)
    if solax_status != 0 or octopus_status != 0:
        return 1
    return command_build_public_snapshot(store)


def command_publish_public_snapshot() -> int:
    try:
        result = publish_public_snapshot()
    except Exception as exc:
        print(
            json.dumps(
                {"publisher": "public_snapshot", "status": "failed", "message": type(exc).__name__}
            )
        )
        return 1
    print(
        json.dumps(
            {
                "publisher": "public_snapshot",
                "status": result.status,
                "message": result.message,
            },
            sort_keys=True,
        )
    )
    return 0


def command_publish_website(store: LiveStore) -> int:
    return command_publish_monthly_summary(store)


def command_publish_monthly_summary(store: LiveStore) -> int:
    try:
        build_monthly_public_snapshot()
        result = publish_monthly_summary(store=store)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "publisher": "monthly_summary",
                    "status": "failed",
                    "message": type(exc).__name__,
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "publisher": "monthly_summary",
                "status": result.status,
                "message": result.message,
                "website_commit_hash": result.website_commit_hash,
            },
            sort_keys=True,
        )
    )
    return 0


def command_update_public_dashboard(store: LiveStore) -> int:
    try:
        with update_lock():
            solax_status = command_collect_solax(store)
            if solax_status != 0:
                return 1
            last_octopus = store.last_successful_run("octopus")
            if last_octopus is None or datetime.now(UTC) - last_octopus >= timedelta(hours=24):
                octopus_status = command_refresh_octopus(store)
                if octopus_status != 0:
                    return 1
            snapshot_status = command_build_public_snapshot(store)
            if snapshot_status != 0:
                return 1
            build_monthly_public_snapshot()
            result = publish_monthly_summary(store=store, use_lock=False)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "publisher": "public_dashboard",
                    "status": "failed",
                    "message": type(exc).__name__,
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "publisher": "public_dashboard",
                "status": result.status,
                "message": result.message,
                "website_commit_hash": result.website_commit_hash,
            },
            sort_keys=True,
        )
    )
    return 0


def run(collector: str, started: datetime, status: str, message: str) -> CollectorRun:
    return CollectorRun(
        collector=collector,
        started_at=started,
        completed_at=datetime.now(UTC),
        status=status,
        message=message,
    )
