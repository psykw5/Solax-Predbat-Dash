"""CLI for read-only Wattson live collection."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from live.config import assert_private_outputs_ignored, public_delay_minutes
from live.models import CollectorRun, QualityEvent
from live.octopus import refresh_octopus_tariffs
from live.snapshot import build_public_snapshot
from live.solax import collect_solax_observation
from live.store import LiveStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Wattson live read-only collection.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect-solax")
    subparsers.add_parser("refresh-octopus")
    subparsers.add_parser("build-public-snapshot")
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


def run(collector: str, started: datetime, status: str, message: str) -> CollectorRun:
    return CollectorRun(
        collector=collector,
        started_at=started,
        completed_at=datetime.now(UTC),
        status=status,
        message=message,
    )
