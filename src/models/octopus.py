"""Models for Octopus tariff ingestion and financial calculations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OctopusAgreement(BaseModel):
    agreement_id: str
    meter_point_id: str
    direction: str
    tariff_code: str
    product_code: str
    valid_from: datetime
    valid_to: datetime | None = None
    source_endpoint: str
    ingestion_timestamp: datetime


class OctopusRate(BaseModel):
    agreement_id: str
    direction: str
    tariff_code: str
    product_code: str
    rate_type: str
    value_inc_vat: float
    valid_from: datetime
    valid_to: datetime | None = None
    payment_method: str | None = None
    source_endpoint: str
    ingestion_timestamp: datetime


class FinancialSummary(BaseModel):
    confirmed_avoided_import_value: float
    confirmed_export_income: float
    confirmed_financial_benefit: float
    estimated_uncovered_export_value: float = 0.0
    estimated_lifetime_financial_benefit: float
    energy_coverage_percentage: float
    tariff_coverage_percentage: float
    excluded_intervals: int
    avoided_import_status: str
    export_income_status: str
    total_financial_benefit_status: str
    generated_at: datetime
    period_start: datetime | None = None
    period_end: datetime | None = None
    notes: list[str] = Field(default_factory=list)
