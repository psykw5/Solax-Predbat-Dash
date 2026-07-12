"""Strongly typed metrics results."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class EnergyTotal(BaseModel):
    """A kWh total over a time range."""

    model_config = ConfigDict(frozen=True)

    metric: str
    start: datetime
    end: datetime
    kwh: float = Field(ge=0)
    interval_count: int = Field(ge=0)
    quality_flagged_interval_count: int = Field(ge=0)


class SelfConsumptionMetric(BaseModel):
    """Self-consumption result for a time range."""

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime
    generation_kwh: float = Field(ge=0)
    export_kwh: float = Field(ge=0)
    self_consumed_kwh: float = Field(ge=0)
    self_consumption_ratio: float | None = Field(default=None, ge=0, le=1)
    self_consumption_percent: float | None = Field(default=None, ge=0, le=100)
    interval_count: int = Field(ge=0)
    quality_flagged_interval_count: int = Field(ge=0)


class DailyEnergySummary(BaseModel):
    """Energy summary for one local calendar day."""

    model_config = ConfigDict(frozen=True)

    date: date
    start: datetime
    end: datetime
    generation_kwh: float = Field(ge=0)
    import_kwh: float = Field(ge=0)
    export_kwh: float = Field(ge=0)
    consumption_kwh: float = Field(ge=0)
    self_consumed_kwh: float = Field(ge=0)
    self_consumption_ratio: float | None = Field(default=None, ge=0, le=1)
    self_consumption_percent: float | None = Field(default=None, ge=0, le=100)
    interval_count: int = Field(ge=0)
    quality_flagged_interval_count: int = Field(ge=0)


class MonthlyEnergySummary(BaseModel):
    """Energy summary for one calendar month."""

    model_config = ConfigDict(frozen=True)

    year: int
    month: int = Field(ge=1, le=12)
    start: datetime
    end: datetime
    generation_kwh: float = Field(ge=0)
    import_kwh: float = Field(ge=0)
    export_kwh: float = Field(ge=0)
    consumption_kwh: float = Field(ge=0)
    self_consumed_kwh: float = Field(ge=0)
    self_consumption_ratio: float | None = Field(default=None, ge=0, le=1)
    self_consumption_percent: float | None = Field(default=None, ge=0, le=100)
    interval_count: int = Field(ge=0)
    quality_flagged_interval_count: int = Field(ge=0)
    days: tuple[DailyEnergySummary, ...]


class AnnualEnergySummary(BaseModel):
    """Energy summary for one calendar year."""

    model_config = ConfigDict(frozen=True)

    year: int
    start: datetime
    end: datetime
    generation_kwh: float = Field(ge=0)
    import_kwh: float = Field(ge=0)
    export_kwh: float = Field(ge=0)
    consumption_kwh: float = Field(ge=0)
    self_consumed_kwh: float = Field(ge=0)
    self_consumption_ratio: float | None = Field(default=None, ge=0, le=1)
    self_consumption_percent: float | None = Field(default=None, ge=0, le=100)
    interval_count: int = Field(ge=0)
    quality_flagged_interval_count: int = Field(ge=0)
    months: tuple[MonthlyEnergySummary, ...]
