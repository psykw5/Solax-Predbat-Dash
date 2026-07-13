"""Incremental read-only Octopus tariff refresh for live snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ingestion.octopus_client import OctopusClient, build_rates_url, write_sanitized_json
from ingestion.octopus_pipeline import (
    extract_electricity_agreements,
    overlaps_period,
    parse_datetime,
    parse_optional_datetime,
)
from live.config import DEFAULT_RAW_LIVE_DIR, OCTOPUS_KEYS, require_credentials
from live.models import TariffSnapshot


def refresh_octopus_tariffs(
    previous_products: dict[str, str] | None = None,
    raw_dir: Path = DEFAULT_RAW_LIVE_DIR / "octopus",
    now: datetime | None = None,
) -> tuple[list[TariffSnapshot], bool]:
    captured_at = now or datetime.now(UTC)
    credentials = require_credentials(OCTOPUS_KEYS)
    client = OctopusClient(credentials["OCTOPUS_API_KEY"])
    account_url, account_payload = client.account(credentials["OCTOPUS_ACCOUNT_NUMBER"])
    write_sanitized_json(raw_dir / "account-active.json", account_payload)

    agreements = extract_electricity_agreements(account_payload, account_url, captured_at)
    active = [
        agreement
        for agreement in agreements
        if overlaps_period(
            agreement.valid_from,
            agreement.valid_to,
            captured_at,
            captured_at + timedelta(seconds=1),
        )
    ]
    snapshots: list[TariffSnapshot] = []
    full_backfill_required = False
    for agreement in active:
        if previous_products and previous_products.get(agreement.direction) not in {
            None,
            agreement.product_code,
        }:
            full_backfill_required = True
        rates = fetch_current_and_upcoming_rates(client, agreement, captured_at)
        write_sanitized_json(
            raw_dir / f"{agreement.direction}-active-rates.json",
            {"endpoint": "active tariff rates", "results": rates},
        )
        current, upcoming = split_current_and_next_rate(rates, captured_at)
        if current is None:
            continue
        snapshots.append(
            TariffSnapshot(
                direction=agreement.direction,
                tariff_code=agreement.tariff_code,
                product_code=agreement.product_code,
                rate_inc_vat=float(current["value_inc_vat"]),
                valid_from=parse_datetime(current["valid_from"]),
                valid_to=parse_optional_datetime(current.get("valid_to")),
                next_rate_inc_vat=None if upcoming is None else float(upcoming["value_inc_vat"]),
                next_valid_from=None
                if upcoming is None
                else parse_datetime(upcoming["valid_from"]),
                captured_at=captured_at,
            )
        )
    return snapshots, full_backfill_required


def fetch_current_and_upcoming_rates(
    client: OctopusClient, agreement: Any, now: datetime
) -> list[dict[str, Any]]:
    period_from = now - timedelta(days=1)
    period_to = now + timedelta(days=2)
    rates: list[dict[str, Any]] = []
    for rate_type in ("standard-unit-rates", "day-unit-rates", "night-unit-rates"):
        url = build_rates_url(
            agreement.product_code,
            agreement.tariff_code,
            rate_type,
            period_from,
            period_to,
        )
        try:
            rates.extend(client.paged(url))
        except Exception:
            if rate_type == "standard-unit-rates":
                raise
    return sorted(rates, key=lambda item: item.get("valid_from") or "")


def split_current_and_next_rate(
    rates: list[dict[str, Any]], now: datetime
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current = None
    upcoming = None
    for rate in rates:
        valid_from = parse_datetime(rate["valid_from"])
        valid_to = parse_optional_datetime(rate.get("valid_to"))
        if valid_from <= now and (valid_to is None or valid_to > now):
            current = rate
        elif valid_from > now and upcoming is None:
            upcoming = rate
    return current, upcoming
