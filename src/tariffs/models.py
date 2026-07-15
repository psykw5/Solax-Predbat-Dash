"""Typed tariff what-if models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BatteryAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    usable_capacity_kwh: float = Field(default=6.0, gt=0)
    initial_soc_kwh: float = Field(default=3.0, ge=0)
    minimum_soc_kwh: float = Field(default=0.6, ge=0)
    maximum_soc_kwh: float = Field(default=6.0, gt=0)
    charge_power_kw: float = Field(default=3.0, gt=0)
    discharge_power_kw: float = Field(default=3.0, gt=0)
    charge_efficiency: float = Field(default=0.95, gt=0, le=1)
    discharge_efficiency: float = Field(default=0.95, gt=0, le=1)
    allow_grid_to_battery: bool = True
    allow_battery_export: bool = False
    allow_simultaneous_import_export_arbitrage: bool = False


class TariffScenario(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    import_product_code: str
    export_product_code: str
    eligibility_status: str
    eligibility_evidence: str
    retrieval_date: datetime
    standing_charge_p_per_day: float = Field(ge=0)
    vat_included: bool = True
    notes: tuple[str, ...] = ()


class ScenarioResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    strategy: str
    eligibility_status: str
    import_energy_cost_gbp: float
    export_income_gbp: float
    standing_charges_gbp: float
    net_electricity_cost_gbp: float
    no_solar_cost_gbp: float
    financial_benefit_vs_no_solar_gbp: float
    difference_vs_flux_gbp: float | None = None
    annualised_difference_vs_flux_gbp: float | None = None
    tariff_coverage_percentage: float
    battery_throughput_kwh: float = 0.0
    equivalent_full_battery_cycles: float = 0.0
    cheap_import_percentage: float | None = None
    high_value_export_percentage: float | None = None
    data_quality_status: str
    assumptions: dict[str, object] = Field(default_factory=dict)


class TariffQualityEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: str
    severity: str
    scenario: str
    message: str
    count: int = 0
