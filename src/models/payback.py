"""Models for discounted payback and NPV projections."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class CapitalEvent(BaseModel):
    """Dated capital cash flow such as a replacement or upgrade."""

    event_date: date
    amount_gbp: float
    category: str
    description: str = ""


class PaybackSummary(BaseModel):
    installation_cost: float
    annual_discount_rate: float
    effective_monthly_discount_rate: float
    installation_date: date
    confirmed_lifetime_nominal_benefit: float
    discounted_historical_benefit: float
    nominal_recovery_percentage: float
    discounted_recovery_percentage: float
    current_npv: float
    projected_simple_payback_month: str | None = None
    projected_discounted_payback_month: str | None = None
    projection_end_date: date
    calculation_status: str
    modelling_assumptions: list[str] = Field(default_factory=list)
    generated_at: datetime
