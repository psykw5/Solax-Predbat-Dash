"""SQLite persistence for read-only live operational data."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from live.config import DEFAULT_DB_PATH
from live.models import CollectorRun, QualityEvent, SolaXObservation, TariffSnapshot


class LiveStore:
    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
        self.connection.executescript(
            """
            create table if not exists solax_observations (
                observation_timestamp text primary key,
                received_at text not null,
                pv_power_kw real,
                battery_soc_percent real,
                battery_power_kw real,
                battery_direction text,
                grid_power_kw real,
                grid_direction text,
                inverter_output_kw real,
                daily_generation_kwh real,
                cumulative_generation_kwh real,
                source_status text not null,
                quality_flags_json text not null
            );
            create table if not exists tariff_snapshots (
                direction text not null,
                tariff_code text not null,
                product_code text not null,
                rate_inc_vat real not null,
                valid_from text not null,
                valid_to text,
                next_rate_inc_vat real,
                next_valid_from text,
                source_status text not null,
                captured_at text not null,
                primary key (direction, tariff_code, valid_from, captured_at)
            );
            create table if not exists collector_runs (
                collector text not null,
                started_at text not null,
                completed_at text not null,
                status text not null,
                message text not null,
                primary key (collector, started_at)
            );
            create table if not exists quality_events (
                event_type text not null,
                severity text not null,
                message text not null,
                observed_at text not null,
                primary key (event_type, observed_at, message)
            );
            """
        )
        self.connection.commit()

    def insert_solax_observation(self, observation: SolaXObservation) -> bool:
        cursor = self.connection.execute(
            """
            insert or ignore into solax_observations values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.observation_timestamp.isoformat(),
                observation.received_at.isoformat(),
                observation.pv_power_kw,
                observation.battery_soc_percent,
                observation.battery_power_kw,
                observation.battery_direction,
                observation.grid_power_kw,
                observation.grid_direction,
                observation.inverter_output_kw,
                observation.daily_generation_kwh,
                observation.cumulative_generation_kwh,
                observation.source_status,
                json.dumps(observation.quality_flags),
            ),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def latest_valid_solax_observation(
        self, before: datetime | None = None
    ) -> SolaXObservation | None:
        query = "select * from solax_observations where source_status = 'valid'"
        params: list[str] = []
        if before is not None:
            query += " and observation_timestamp <= ?"
            params.append(before.isoformat())
        query += " order by observation_timestamp desc limit 1"
        row = self.connection.execute(query, params).fetchone()
        return None if row is None else solax_from_row(row)

    def insert_tariff_snapshots(self, snapshots: list[TariffSnapshot]) -> None:
        self.connection.executemany(
            """
            insert or ignore into tariff_snapshots values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot.direction,
                    snapshot.tariff_code,
                    snapshot.product_code,
                    snapshot.rate_inc_vat,
                    snapshot.valid_from.isoformat(),
                    None if snapshot.valid_to is None else snapshot.valid_to.isoformat(),
                    snapshot.next_rate_inc_vat,
                    None
                    if snapshot.next_valid_from is None
                    else snapshot.next_valid_from.isoformat(),
                    snapshot.source_status,
                    snapshot.captured_at.isoformat(),
                )
                for snapshot in snapshots
            ],
        )
        self.connection.commit()

    def latest_tariff_snapshot(self, direction: str) -> TariffSnapshot | None:
        row = self.connection.execute(
            """
            select * from tariff_snapshots
            where direction = ?
            order by captured_at desc, valid_from desc
            limit 1
            """,
            (direction,),
        ).fetchone()
        return None if row is None else tariff_from_row(row)

    def previous_products(self) -> dict[str, str]:
        products: dict[str, str] = {}
        for direction in ("import", "export"):
            snapshot = self.latest_tariff_snapshot(direction)
            if snapshot is not None:
                products[direction] = snapshot.product_code
        return products

    def last_successful_run(self, collector: str) -> datetime | None:
        row = self.connection.execute(
            """
            select completed_at from collector_runs
            where collector = ? and status = 'success'
            order by completed_at desc
            limit 1
            """,
            (collector,),
        ).fetchone()
        return None if row is None else datetime.fromisoformat(row["completed_at"])

    def insert_collector_run(self, run: CollectorRun) -> None:
        self.connection.execute(
            "insert or replace into collector_runs values (?, ?, ?, ?, ?)",
            (
                run.collector,
                run.started_at.isoformat(),
                run.completed_at.isoformat(),
                run.status,
                run.message,
            ),
        )
        self.connection.commit()

    def insert_quality_event(self, event: QualityEvent) -> None:
        self.connection.execute(
            "insert or ignore into quality_events values (?, ?, ?, ?)",
            (event.event_type, event.severity, event.message, event.observed_at.isoformat()),
        )
        self.connection.commit()


def solax_from_row(row: sqlite3.Row) -> SolaXObservation:
    return SolaXObservation(
        observation_timestamp=datetime.fromisoformat(row["observation_timestamp"]),
        received_at=datetime.fromisoformat(row["received_at"]),
        pv_power_kw=row["pv_power_kw"],
        battery_soc_percent=row["battery_soc_percent"],
        battery_power_kw=row["battery_power_kw"],
        battery_direction=row["battery_direction"],
        grid_power_kw=row["grid_power_kw"],
        grid_direction=row["grid_direction"],
        inverter_output_kw=row["inverter_output_kw"],
        daily_generation_kwh=row["daily_generation_kwh"],
        cumulative_generation_kwh=row["cumulative_generation_kwh"],
        source_status=row["source_status"],
        quality_flags=json.loads(row["quality_flags_json"]),
    )


def tariff_from_row(row: sqlite3.Row) -> TariffSnapshot:
    return TariffSnapshot(
        direction=row["direction"],
        tariff_code=row["tariff_code"],
        product_code=row["product_code"],
        rate_inc_vat=row["rate_inc_vat"],
        valid_from=datetime.fromisoformat(row["valid_from"]),
        valid_to=None if row["valid_to"] is None else datetime.fromisoformat(row["valid_to"]),
        next_rate_inc_vat=row["next_rate_inc_vat"],
        next_valid_from=None
        if row["next_valid_from"] is None
        else datetime.fromisoformat(row["next_valid_from"]),
        source_status=row["source_status"],
        captured_at=datetime.fromisoformat(row["captured_at"]),
    )
