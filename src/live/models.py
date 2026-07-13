"""Typed models for live telemetry, tariff snapshots and public summaries."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SolaXObservation(BaseModel):
    observation_timestamp: datetime
    received_at: datetime
    pv_power_kw: float | None = None
    battery_soc_percent: float | None = None
    battery_power_kw: float | None = None
    battery_direction: str | None = None
    grid_power_kw: float | None = None
    grid_direction: str | None = None
    inverter_output_kw: float | None = None
    daily_generation_kwh: float | None = None
    cumulative_generation_kwh: float | None = None
    source_status: str = "valid"
    quality_flags: list[str] = Field(default_factory=list)


class TariffSnapshot(BaseModel):
    direction: str
    tariff_code: str
    product_code: str
    rate_inc_vat: float
    valid_from: datetime
    valid_to: datetime | None = None
    next_rate_inc_vat: float | None = None
    next_valid_from: datetime | None = None
    source_status: str = "active"
    captured_at: datetime


class CollectorRun(BaseModel):
    collector: str
    started_at: datetime
    completed_at: datetime
    status: str
    message: str = ""


class QualityEvent(BaseModel):
    event_type: str
    severity: str
    message: str
    observed_at: datetime


class PublicSnapshot(BaseModel):
    generated_at: str
    data_as_of: str
    current_pv_power_kw: float | None
    current_battery_percentage: float | None
    current_battery_direction: str | None
    current_battery_power_kw: float | None
    current_grid_direction: str | None
    current_grid_power_kw: float | None
    todays_generation_kwh: float | None
    current_import_rate_p_per_kwh: float | None
    current_export_rate_p_per_kwh: float | None
    next_tariff_change: str | None
    next_rate_p_per_kwh: float | None
    confirmed_lifetime_financial_benefit_gbp: float | None
    nominal_recovery_percentage: float | None
    discounted_recovery_percentage: float | None
    simple_payback_month: str | None
    discounted_payback_month: str | None
    health_status: str
    freshness_minutes: int
