"""Typed central configuration models for Wattson."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SiteConfig(StrictModel):
    public_region: str
    timezone: str

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value


class SolarOrientationConfig(StrictModel):
    description: str
    azimuth_deg: float = Field(ge=0, le=360)


class SolarConfig(StrictModel):
    panel_count: int = Field(gt=0)
    panel_rating_kw: float = Field(gt=0)
    installed_capacity_kwp: float = Field(gt=0)
    orientation: SolarOrientationConfig
    roof_pitch_deg: float = Field(ge=0, le=90)
    significant_shading: bool
    mounting: Literal["fixed_roof"]

    @model_validator(mode="after")
    def installed_capacity_must_match_panel_rating(self) -> SolarConfig:
        expected = self.panel_count * self.panel_rating_kw
        if abs(expected - self.installed_capacity_kwp) > 0.001:
            raise ValueError(
                "solar.panel_count * solar.panel_rating_kw must equal "
                "solar.installed_capacity_kwp within 0.001 kWp."
            )
        return self


class InverterConfig(StrictModel):
    manufacturer: str
    model: str


class BatteryConfig(StrictModel):
    manufacturer: str
    model_family: str
    nominal_capacity_kwh: float = Field(gt=0)
    usable_capacity_kwh: float = Field(gt=0)
    minimum_soc_percent: float = Field(ge=0, le=100)
    maximum_soc_percent: float = Field(ge=0, le=100)
    charge_power_limit_kw: float | None = Field(default=None, gt=0)
    discharge_power_limit_kw: float | None = Field(default=None, gt=0)
    charge_efficiency: float | None = Field(default=None, gt=0, le=1)
    discharge_efficiency: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def soc_range_must_be_ordered(self) -> BatteryConfig:
        if self.minimum_soc_percent >= self.maximum_soc_percent:
            raise ValueError("battery.minimum_soc_percent must be less than maximum_soc_percent.")
        return self


class TariffConfig(StrictModel):
    supplier: str
    current_import_product: str
    current_export_product: str


class FinancialConfig(StrictModel):
    installation_cost_gbp: float = Field(gt=0)
    installation_date: date
    discount_rate_annual: float = Field(ge=0, le=1)
    include_degradation: bool
    public_finance_model: Literal["opportunity_cost"]


class PvgisConfig(StrictModel):
    enabled: bool
    system_loss_percent: float | None = Field(default=None, ge=0, le=100)


class WeatherConfig(StrictModel):
    provider: Literal["open_meteo"]
    forecast_collection_interval_hours: int = Field(gt=0)
    observation_refresh_interval_hours: int = Field(gt=0)
    pvgis: PvgisConfig


class CollectionConfig(StrictModel):
    solax_interval_minutes: int = Field(gt=0)
    octopus_refresh_interval_hours: int = Field(gt=0)
    public_update_frequency: Literal["monthly"]


class PublicationConfig(StrictModel):
    public_summary_filename: Literal["wattson-summary.json"]
    public_location_precision: Literal["region_only"]
    allow_live_household_data: bool

    @field_validator("allow_live_household_data")
    @classmethod
    def live_household_data_must_stay_private(cls, value: bool) -> bool:
        if value:
            raise ValueError("Public publisher cannot expose live household data.")
        return value


class WattsonConfig(StrictModel):
    schema_version: Literal[1]
    site: SiteConfig
    solar: SolarConfig
    inverter: InverterConfig
    battery: BatteryConfig
    tariff: TariffConfig
    financial: FinancialConfig
    weather: WeatherConfig
    collection: CollectionConfig
    publication: PublicationConfig

    @model_validator(mode="before")
    @classmethod
    def reject_private_location_keys(cls, data: Any, _info: ValidationInfo) -> Any:
        forbidden = {
            "latitude",
            "longitude",
            "lat",
            "lon",
            "lng",
            "coordinates",
            "postcode",
            "address",
        }
        found: list[str] = []

        def walk(value: Any, path: str = "") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key).lower()
                    child_path = f"{path}.{key_text}" if path else key_text
                    if key_text in forbidden:
                        found.append(child_path)
                    walk(child, child_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{path}[{index}]")

        walk(data)
        if found:
            raise ValueError(
                "Exact private location fields must stay in .env, not YAML: "
                + ", ".join(sorted(found))
            )
        return data
