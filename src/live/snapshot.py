"""Build strictly sanitised public Wattson live snapshots."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from live.config import DEFAULT_PUBLIC_SNAPSHOT_PATH
from live.models import PublicSnapshot
from live.store import LiveStore

FINANCIAL_SUMMARY_PATH = Path("data/processed/financial/lifetime_summary.json")
PAYBACK_SUMMARY_PATH = Path("data/processed/financial/payback_public_summary.json")


def build_public_snapshot(
    store: LiveStore,
    output_path: Path = DEFAULT_PUBLIC_SNAPSHOT_PATH,
    delay_minutes: int = 30,
    now: datetime | None = None,
) -> PublicSnapshot:
    if delay_minutes < 0:
        raise ValueError("Public data delay cannot be negative.")
    generated_at = now or datetime.now(UTC)
    cutoff = generated_at - timedelta(minutes=delay_minutes)
    observation = store.latest_valid_solax_observation(before=cutoff)
    import_rate = store.latest_tariff_snapshot("import")
    export_rate = store.latest_tariff_snapshot("export")
    if observation is None:
        raise ValueError("No valid delayed SolaX observation is available for publication.")
    if import_rate is None or export_rate is None:
        raise ValueError("Import and export tariff snapshots are required for publication.")

    financial = read_json(FINANCIAL_SUMMARY_PATH)
    payback = read_json(PAYBACK_SUMMARY_PATH)
    if not financial or not payback:
        raise ValueError("Financial and payback summaries are required for publication.")

    freshness = int((generated_at - observation.observation_timestamp).total_seconds() // 60)
    snapshot = PublicSnapshot(
        generated_at=minute_iso(generated_at),
        data_as_of=minute_iso(observation.observation_timestamp),
        current_pv_power_kw=round_optional(observation.pv_power_kw, 2),
        current_battery_percentage=round_optional(observation.battery_soc_percent, 0),
        current_battery_direction=observation.battery_direction,
        current_battery_power_kw=round_optional(observation.battery_power_kw, 2),
        current_grid_direction=observation.grid_direction,
        current_grid_power_kw=round_optional(observation.grid_power_kw, 2),
        todays_generation_kwh=round_optional(observation.daily_generation_kwh, 1),
        current_import_rate_p_per_kwh=round_optional(import_rate.rate_inc_vat, 3),
        current_export_rate_p_per_kwh=round_optional(export_rate.rate_inc_vat, 3),
        next_tariff_change=next_change(import_rate, export_rate),
        next_rate_p_per_kwh=next_rate(import_rate, export_rate),
        confirmed_lifetime_financial_benefit_gbp=financial.get("confirmed_financial_benefit"),
        nominal_recovery_percentage=payback.get("nominal_recovery_percentage"),
        discounted_recovery_percentage=payback.get("discounted_recovery_percentage"),
        simple_payback_month=payback.get("projected_simple_payback_month"),
        discounted_payback_month=payback.get("projected_discounted_payback_month"),
        health_status="ok",
        freshness_minutes=freshness,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(snapshot.model_dump(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return snapshot


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def minute_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def round_optional(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def next_change(import_rate: object, export_rate: object) -> str | None:
    candidates = [
        value
        for value in [
            getattr(import_rate, "next_valid_from", None),
            getattr(export_rate, "next_valid_from", None),
        ]
        if value is not None
    ]
    if not candidates:
        return None
    return minute_iso(min(candidates))


def next_rate(import_rate: object, export_rate: object) -> float | None:
    candidates = [
        (
            getattr(import_rate, "next_valid_from", None),
            getattr(import_rate, "next_rate_inc_vat", None),
        ),
        (
            getattr(export_rate, "next_valid_from", None),
            getattr(export_rate, "next_rate_inc_vat", None),
        ),
    ]
    candidates = [(date_value, rate) for date_value, rate in candidates if date_value is not None]
    if not candidates:
        return None
    return round(float(min(candidates, key=lambda item: item[0])[1]), 3)
