# Domain Model

Design date: 2026-07-12

Scope: core domain entities for the home energy optimisation platform. This document defines the language of the system before implementation. Field names are proposed logical names, not final database column names.

## Principles

- Use UTC timestamps internally and convert to `Europe/London` only for display and tariff interpretation.
- Treat measured, forecast, simulated, and recommended values as separate concepts.
- Keep settled supplier data separate from provisional inverter or Home Assistant telemetry.
- Record source, quality, and retrieval metadata wherever data may be delayed, missing, estimated, or corrected.
- Store financial values in GBP and tariff unit rates in GBP/kWh unless explicitly stated otherwise.

## MVP Scope

Immediate MVP: import historical SolaX and Octopus data, store it in PostgreSQL, and calculate estimated savings to date. The MVP should be read-only and historical; it should not include recommendations, simulation, autonomous control, or a full dashboard implementation.

MVP entities:

- `Site`
- `Device`
- `DataSource`
- `SolarReading`
- `BatteryReading`
- `GridReading`
- `LoadReading`, only if household load is available from SolaX history or can be reliably derived.
- `MeterPoint`
- `MeterConsumptionInterval`
- `TariffProduct`
- `TariffAgreement`
- `TariffInterval`
- `FinancialInterval`
- `IngestionRun`
- `DataQualityEvent`, limited to ingestion completeness and obvious invalid values.

MVP metric definitions:

- `AvoidedImportCost`
- `ExportIncome`
- `TotalFinancialBenefit`
- `SelfConsumption`, only if SolaX export and generation history are sufficiently granular.
- `GridIndependence`, only if import and load data can be trusted for the same intervals.
- `BatteryThroughput`, only if historical charge/discharge energy is available or derivable.

Deferred entities:

- `WeatherObservation`
- `ForecastRun`
- `ForecastInterval`
- `EnergyFlowInterval`
- `Recommendation`
- `ControlAction`
- `SimulationScenario`
- `SimulationRun`
- `UserDecision`

Deferred scope:

- Weather-based forecasting.
- Recommendation generation.
- Future what-if analysis.
- Autonomous control.
- User decision tracking.
- Detailed energy-flow attribution, unless the MVP data proves granular enough to calculate it without guessing.
- Battery degradation modelling.

MVP database tables should be the minimum required to support the MVP entities above. Prefer simple raw/import tables plus tested derived SQL views for savings calculations. Do not implement deferred entities as empty tables just because they are part of the future model.

## Site

Represents the physical home or installation being monitored and optimised.

Fields:

- `site_id`: Stable identifier.
- `name`: Human-readable site name.
- `timezone`: Site timezone, expected to be `Europe/London`.
- `latitude`: Site latitude.
- `longitude`: Site longitude.
- `postcode_region`: Optional UK region or tariff area reference.
- `created_at`: Time the site was registered.
- `active`: Whether this site is currently active.

Relationships:

- Owns devices, meter points, readings, tariffs, forecasts, recommendations, and simulations.

## Device

Represents a physical or logical device such as inverter, battery, meter, gateway, or Home Assistant integration.

Fields:

- `device_id`: Stable identifier.
- `site_id`: Parent site.
- `manufacturer`: Device manufacturer, for example `SolaX`.
- `model`: Device model, for example `X1 Hybrid G4`.
- `serial_number`: Manufacturer serial number where available.
- `firmware_version`: Firmware version where available.
- `device_role`: Role such as `inverter`, `battery`, `meter`, `gateway`, `weather_source`, or `controller`.
- `connection_type`: Connection route such as `home_assistant`, `modbus_tcp`, `modbus_rtu`, `local_rest`, or `cloud_api`.
- `metadata`: Device-specific attributes.
- `active`: Whether this device is currently in use.

Relationships:

- Produces device readings.
- May be the target of future control actions.

## DataSource

Represents an external system, integration, collector, or API that supplies data.

Fields:

- `source_id`: Stable identifier.
- `name`: Human-readable source name.
- `source_type`: Type such as `home_assistant`, `solax_local`, `solax_modbus`, `solax_cloud`, `octopus`, `open_meteo`, or `manual`.
- `base_url`: API or service URL where applicable.
- `poll_interval_seconds`: Expected polling interval.
- `retention_policy`: Known source retention constraints.
- `authentication_method`: Method such as token, basic auth, local password, or none.
- `active`: Whether the source is enabled.

Relationships:

- Attached to readings, tariff intervals, forecasts, and ingestion health events.

## SolarReading

Represents measured or estimated solar generation for a point or interval.

Fields:

- `reading_id`: Stable identifier.
- `site_id`: Parent site.
- `device_id`: Inverter or generation meter.
- `source_id`: Source that supplied the reading.
- `timestamp`: Observation timestamp for point readings.
- `interval_start`: Start of interval where interval data is available.
- `interval_end`: End of interval where interval data is available.
- `generated_kw`: Solar generation power in kW.
- `generated_kwh`: Solar generation energy in kWh over the interval.
- `temperature_c`: Inverter, panel, or ambient temperature if supplied by the source.
- `irradiance_w_m2`: Solar irradiance if measured or joined from weather data.
- `quality`: Quality flag such as `observed`, `estimated`, `missing`, `corrected`, or `provisional`.
- `raw_payload`: Original source payload where useful.

Relationships:

- Used by energy flow, self-consumption, avoided import cost, forecasting, and simulation.

## BatteryReading

Represents measured battery state and power flow.

Fields:

- `reading_id`: Stable identifier.
- `site_id`: Parent site.
- `device_id`: Battery or hybrid inverter.
- `source_id`: Source that supplied the reading.
- `timestamp`: Observation timestamp.
- `interval_start`: Start of interval where interval data is available.
- `interval_end`: End of interval where interval data is available.
- `soc`: Battery state of charge as a percentage.
- `charge_kw`: Battery charging power in kW.
- `discharge_kw`: Battery discharging power in kW.
- `charge_kwh`: Energy charged over the interval.
- `discharge_kwh`: Energy discharged over the interval.
- `cycles`: Equivalent full cycles if supplied or calculated.
- `usable_capacity_kwh`: Usable capacity assumed for calculations.
- `temperature_c`: Battery temperature if available.
- `mode`: Operating mode such as idle, charging, discharging, forced charge, or forced discharge.
- `quality`: Quality flag.
- `raw_payload`: Original source payload where useful.

Relationships:

- Used by battery performance, throughput, grid independence, recommendation, and simulation calculations.

## GridReading

Represents measured grid import and export power or energy.

Fields:

- `reading_id`: Stable identifier.
- `site_id`: Parent site.
- `device_id`: Inverter, meter, or Home Assistant source.
- `source_id`: Source that supplied the reading.
- `timestamp`: Observation timestamp.
- `interval_start`: Start of interval where interval data is available.
- `interval_end`: End of interval where interval data is available.
- `import_kw`: Grid import power in kW.
- `export_kw`: Grid export power in kW.
- `import_kwh`: Grid import energy over the interval.
- `export_kwh`: Grid export energy over the interval.
- `net_grid_kw`: Export-positive or import-positive net grid value, using a documented sign convention.
- `quality`: Quality flag.
- `raw_payload`: Original source payload where useful.

Relationships:

- Used by import cost, export income, self-consumption, grid independence, and data quality checks.

## LoadReading

Represents household consumption or load.

Fields:

- `reading_id`: Stable identifier.
- `site_id`: Parent site.
- `source_id`: Source that supplied or calculated the reading.
- `timestamp`: Observation timestamp.
- `interval_start`: Start of interval where interval data is available.
- `interval_end`: End of interval where interval data is available.
- `load_kw`: Household load power in kW.
- `load_kwh`: Household load energy over the interval.
- `calculation_method`: Method such as direct sensor, inverter reported, or derived energy balance.
- `quality`: Quality flag.
- `raw_payload`: Original source payload where useful.

Relationships:

- Used by grid independence, energy flows, baseline cost, recommendation, and simulation.

## MeterPoint

Represents an Octopus electricity import/export meter point or gas meter point.

Fields:

- `meter_point_id`: Stable identifier.
- `site_id`: Parent site.
- `direction`: `import`, `export`, or `gas`.
- `mpan_mprn`: MPAN for electricity or MPRN for gas.
- `meter_serial_number`: Physical meter serial number.
- `is_export`: Whether this meter point records export.
- `profile_class`: Electricity profile class if available.
- `region_code`: Octopus/GSP region if available.
- `active`: Whether this meter point is active.

Relationships:

- Has tariff agreements and settled consumption intervals.

## MeterConsumptionInterval

Represents settled or supplier-provided consumption/export data for a meter interval.

Fields:

- `meter_point_id`: Parent meter point.
- `source_id`: Usually Octopus.
- `interval_start`: Interval start.
- `interval_end`: Interval end.
- `consumption_kwh`: Energy quantity for the interval.
- `direction`: `import`, `export`, or `gas`.
- `retrieved_at`: Time the interval was retrieved from the supplier.
- `quality`: Quality flag.
- `raw_payload`: Original source payload.

Relationships:

- Used for historical import cost and export income.
- Reconciles live inverter telemetry.

## TariffProduct

Represents an Octopus tariff product such as Flux import or Flux export.

Fields:

- `product_code`: Supplier product code.
- `display_name`: Short name.
- `full_name`: Full product name.
- `direction`: `import` or `export`.
- `brand`: Supplier brand.
- `available_from`: Product availability start.
- `available_to`: Product availability end, if retired.
- `description`: Optional human-readable description.
- `metadata`: Supplier-specific fields.

Relationships:

- Owns tariff agreements and tariff intervals.

## TariffAgreement

Represents the site's agreement to a specific tariff code for a meter point and date range.

Fields:

- `agreement_id`: Stable identifier.
- `site_id`: Parent site.
- `meter_point_id`: Related meter point.
- `product_code`: Related tariff product.
- `tariff_code`: Region/payment-specific tariff code.
- `direction`: `import` or `export`.
- `valid_from`: Agreement start.
- `valid_to`: Agreement end, if known.
- `payment_method`: Payment method where relevant.
- `source_id`: Usually Octopus.
- `raw_payload`: Original source payload.

Relationships:

- Selects which tariff intervals apply to import/export calculations.

## TariffInterval

Represents an import/export price valid for a time interval.

Fields:

- `tariff_interval_id`: Stable identifier.
- `tariff_code`: Supplier tariff code.
- `start`: Interval start.
- `end`: Interval end.
- `import_price`: Import unit price in GBP/kWh where applicable.
- `export_price`: Export unit price in GBP/kWh where applicable.
- `standing_charge`: Standing charge in GBP/day where applicable.
- `rate_type`: Type such as standard, day, night, peak, off_peak, export, or standing_charge.
- `value_inc_vat`: Price including VAT.
- `value_exc_vat`: Price excluding VAT.
- `currency`: Expected to be `GBP`.
- `source_id`: Source that supplied the tariff.
- `retrieved_at`: Time the price was retrieved.

Relationships:

- Used by import cost, avoided import cost, export income, and what-if analysis.

## WeatherObservation

Represents observed, reanalysis, or forecast weather data for a site and interval.

Fields:

- `weather_observation_id`: Stable identifier.
- `site_id`: Parent site.
- `source_id`: Weather source.
- `timestamp`: Observation timestamp for point data.
- `interval_start`: Start of interval.
- `interval_end`: End of interval.
- `cloud_cover`: Cloud cover percentage.
- `temperature`: Temperature in degrees Celsius.
- `sunshine`: Sunshine duration or sunshine proxy for the interval.
- `irradiance_w_m2`: Global or shortwave irradiance.
- `direct_radiation_w_m2`: Direct radiation.
- `diffuse_radiation_w_m2`: Diffuse radiation.
- `precipitation_mm`: Precipitation.
- `wind_speed_m_s`: Wind speed.
- `model`: Weather model or reanalysis model.
- `run_type`: `forecast`, `historical`, or `observed`.
- `retrieved_at`: Time the data was retrieved.
- `quality`: Quality flag.

Relationships:

- Used by solar forecasting, simulation, recommendations, and data quality context.

## ForecastRun

Represents a retrieved or generated forecast run.

Fields:

- `forecast_run_id`: Stable identifier.
- `site_id`: Parent site.
- `source_id`: Forecast source or internal model.
- `created_at`: Time the run was created or retrieved.
- `forecast_reference_time`: Forecast model reference time.
- `horizon_start`: Forecast horizon start.
- `horizon_end`: Forecast horizon end.
- `model`: Forecast model name or algorithm.
- `model_version`: Forecast model version.
- `inputs`: Input references or parameter summary.
- `quality`: Quality flag.

Relationships:

- Owns forecast intervals for weather, solar generation, load, price, and battery state.

## ForecastInterval

Represents a forecast value for one interval.

Fields:

- `forecast_interval_id`: Stable identifier.
- `forecast_run_id`: Parent forecast run.
- `interval_start`: Interval start.
- `interval_end`: Interval end.
- `forecast_type`: Type such as solar_generation, household_load, import_price, export_price, battery_soc, import_kwh, or export_kwh.
- `value`: Forecast value.
- `unit`: Unit of value.
- `confidence_low`: Optional lower confidence bound.
- `confidence_high`: Optional upper confidence bound.
- `quality`: Quality flag.

Relationships:

- Used by recommendation and simulation.

## EnergyFlowInterval

Represents derived energy movement between sources and sinks over an interval.

Fields:

- `energy_flow_interval_id`: Stable identifier.
- `site_id`: Parent site.
- `interval_start`: Interval start.
- `interval_end`: Interval end.
- `pv_to_load_kwh`: Solar energy consumed directly by load.
- `pv_to_battery_kwh`: Solar energy stored in battery.
- `pv_to_grid_kwh`: Solar energy exported.
- `grid_to_load_kwh`: Grid energy consumed directly by load.
- `grid_to_battery_kwh`: Grid energy stored in battery.
- `battery_to_load_kwh`: Battery energy used by load.
- `battery_to_grid_kwh`: Battery energy exported, if supported.
- `losses_kwh`: Estimated conversion or residual losses.
- `calculation_method`: Derivation method.
- `quality`: Quality flag.

Relationships:

- Used by self-consumption, grid independence, avoided import cost, and battery attribution.

## FinancialInterval

Represents calculated financial values for an interval.

Fields:

- `financial_interval_id`: Stable identifier.
- `site_id`: Parent site.
- `interval_start`: Interval start.
- `interval_end`: Interval end.
- `import_cost`: Cost of imported energy in GBP.
- `export_income`: Income from exported energy in GBP.
- `avoided_import_cost`: Estimated avoided import cost in GBP.
- `standing_charge`: Standing charge allocated to the interval.
- `total_financial_benefit`: Total calculated benefit in GBP.
- `import_price`: Applied import price in GBP/kWh.
- `export_price`: Applied export price in GBP/kWh.
- `calculation_method`: Calculation version or method.
- `quality`: Quality flag.

Relationships:

- Used by financial savings, overview, recommendation comparison, and simulation validation.

## Recommendation

Represents a human-readable optimisation recommendation.

Fields:

- `recommendation_id`: Stable identifier.
- `site_id`: Parent site.
- `created_at`: Time the recommendation was generated.
- `horizon_start`: Start of period affected.
- `horizon_end`: End of period affected.
- `recommendation_type`: Type such as charge_battery, discharge_battery, hold_battery, export_energy, shift_load, or do_nothing.
- `reason`: Human-readable explanation.
- `expected_saving`: Expected saving in GBP.
- `expected_energy_impact_kwh`: Expected energy impact in kWh.
- `confidence`: Confidence score or band.
- `algorithm`: Algorithm or model that generated the recommendation.
- `algorithm_version`: Version of the algorithm.
- `inputs`: References to forecasts, tariffs, readings, and assumptions used.
- `accepted`: Whether the user accepted the recommendation.
- `accepted_at`: Time the recommendation was accepted.
- `executed`: Whether the recommendation was executed.
- `executed_at`: Time execution occurred.
- `execution_status`: proposed, accepted, rejected, scheduled, executed, failed, cancelled, or expired.
- `actual_saving`: Actual saving later measured, where available.
- `quality`: Quality or trust flag.

Relationships:

- May create one or more planned actions.
- May be compared against actual outcomes.

## ControlAction

Represents a planned or executed action against Home Assistant or a device.

Fields:

- `control_action_id`: Stable identifier.
- `site_id`: Parent site.
- `recommendation_id`: Parent recommendation, if applicable.
- `target_device_id`: Device being controlled.
- `created_at`: Time action was created.
- `scheduled_for`: Time action should run.
- `executed_at`: Time action actually ran.
- `action_type`: Type such as set_charge_mode, set_discharge_mode, set_reserve, enable_automation, or disable_automation.
- `parameters`: Action parameters.
- `safety_constraints`: Constraints checked before execution.
- `status`: proposed, scheduled, executing, executed, failed, cancelled, or blocked.
- `result`: Execution result summary.
- `error_message`: Error details if failed.

Relationships:

- Audits autonomous control and manual override behaviour.

## SimulationScenario

Represents a what-if scenario for future or historical evaluation.

Fields:

- `scenario_id`: Stable identifier.
- `site_id`: Parent site.
- `name`: Scenario name.
- `description`: Human-readable description.
- `created_at`: Time scenario was created.
- `horizon_start`: Scenario start.
- `horizon_end`: Scenario end.
- `baseline`: Baseline definition.
- `assumptions`: Tariff, weather, load, battery, and behavioural assumptions.
- `status`: draft, running, completed, failed, or archived.

Relationships:

- Owns simulation runs.

## SimulationRun

Represents the execution of a scenario through an optimisation or energy model.

Fields:

- `simulation_run_id`: Stable identifier.
- `scenario_id`: Parent scenario.
- `site_id`: Parent site.
- `created_at`: Time run was created.
- `completed_at`: Time run completed.
- `model`: Simulation model.
- `model_version`: Simulation model version.
- `inputs`: Input references.
- `outputs`: Output summary.
- `total_financial_benefit`: Simulated total benefit in GBP.
- `total_import_kwh`: Simulated import.
- `total_export_kwh`: Simulated export.
- `battery_throughput_kwh`: Simulated battery throughput.
- `constraint_violations`: Constraint violations summary.
- `status`: running, completed, failed, or cancelled.

Relationships:

- Used by future what-if analysis and recommendation validation.

## DataQualityEvent

Represents a detected issue with source data or derived calculations.

Fields:

- `data_quality_event_id`: Stable identifier.
- `site_id`: Parent site.
- `source_id`: Related source, if applicable.
- `entity_type`: Entity type affected.
- `entity_id`: Affected entity identifier if available.
- `detected_at`: Time issue was detected.
- `interval_start`: Start of affected interval.
- `interval_end`: End of affected interval.
- `severity`: info, warning, error, or critical.
- `category`: missing_data, stale_data, duplicate_data, invalid_value, unit_mismatch, balance_error, delayed_supplier_data, or authentication_error.
- `message`: Human-readable explanation.
- `resolved`: Whether the issue is resolved.
- `resolved_at`: Time issue was resolved.

Relationships:

- Drives data quality dashboard and trust flags.

## IngestionRun

Represents one collector run against a source.

Fields:

- `ingestion_run_id`: Stable identifier.
- `source_id`: Source being collected.
- `site_id`: Parent site.
- `started_at`: Start time.
- `finished_at`: Finish time.
- `status`: running, succeeded, partial, failed, or cancelled.
- `records_read`: Number of source records read.
- `records_written`: Number of records persisted.
- `records_skipped`: Number of records skipped.
- `watermark_start`: Start watermark used.
- `watermark_end`: End watermark reached.
- `error_message`: Error details if failed.

Relationships:

- Provides auditability for data collection and backfills.

## UserDecision

Represents a user response to a recommendation or control prompt.

Fields:

- `user_decision_id`: Stable identifier.
- `recommendation_id`: Related recommendation.
- `site_id`: Parent site.
- `decided_at`: Time of decision.
- `decision`: accepted, rejected, deferred, or overridden.
- `reason`: Optional human-entered reason.
- `actor`: User, system, or automation responsible for the decision.

Relationships:

- Updates recommendation state and informs future recommendation quality.

## Metric Definitions

These are domain-level outputs derived from the entities above.

### AvoidedImportCost

Fields:

- `site_id`
- `period_start`
- `period_end`
- `avoided_import_cost`
- `self_consumed_generation_kwh`
- `import_price_basis`
- `calculation_method`
- `quality`

Definition:

```text
avoided_import_cost =
  sum(self_consumed_generation_kwh_i * import_price_i)
```

## ExportIncome

Fields:

- `site_id`
- `period_start`
- `period_end`
- `export_income`
- `export_kwh`
- `export_price_basis`
- `calculation_method`
- `quality`

Definition:

```text
export_income =
  sum(export_kwh_i * export_price_i)
```

## TotalFinancialBenefit

Fields:

- `site_id`
- `period_start`
- `period_end`
- `avoided_import_cost`
- `export_income`
- `additional_operating_costs`
- `total_financial_benefit`
- `calculation_method`
- `quality`

Definition:

```text
total_financial_benefit =
  avoided_import_cost + export_income - additional_operating_costs
```

## SelfConsumption

Fields:

- `site_id`
- `period_start`
- `period_end`
- `pv_generation_kwh`
- `grid_export_kwh`
- `self_consumed_generation_kwh`
- `self_consumption_percent`
- `calculation_method`
- `quality`

Definition:

```text
self_consumption_percent =
  100 * (pv_generation_kwh - grid_export_kwh) / pv_generation_kwh
```

## GridIndependence

Fields:

- `site_id`
- `period_start`
- `period_end`
- `household_load_kwh`
- `grid_import_kwh`
- `grid_independence_percent`
- `calculation_method`
- `quality`

Definition:

```text
grid_independence_percent =
  100 * (1 - grid_import_kwh / household_load_kwh)
```

## BatteryThroughput

Fields:

- `site_id`
- `period_start`
- `period_end`
- `battery_charge_kwh`
- `battery_discharge_kwh`
- `battery_throughput_kwh`
- `equivalent_full_cycles`
- `usable_capacity_kwh`
- `calculation_method`
- `quality`

Definition:

```text
battery_throughput_kwh =
  battery_charge_kwh + battery_discharge_kwh
```

## Entity Lifecycle

1. `IngestionRun` records data collection from `DataSource`.
2. Raw readings become `SolarReading`, `BatteryReading`, `GridReading`, `LoadReading`, `MeterConsumptionInterval`, `TariffInterval`, and `WeatherObservation`.
3. Derived calculations create `EnergyFlowInterval` and `FinancialInterval`.
4. Forecasting creates `ForecastRun` and `ForecastInterval`.
5. Optimisation creates `Recommendation`.
6. Accepted recommendations may create `ControlAction`.
7. Outcomes are compared with actual readings, financial intervals, and `UserDecision`.
8. Problems are captured as `DataQualityEvent`.

## Open Modelling Questions

- Is household load measured directly by SolaX, or must it be derived from energy balance?
- Can battery charge source be identified as PV versus grid?
- Are battery readings AC-side or DC-side, and is that consistent across charge and discharge?
- Which SolaX values are cumulative counters and which are instantaneous readings?
- How should same-day provisional inverter data be reconciled against delayed Octopus settled data?
- Should standing charges be allocated across intervals or shown only as daily fixed costs?
- Should export income use VAT-inclusive or VAT-exclusive rates for all dashboard views?
- What confidence scale should recommendations use: numeric 0-1, percentage, or labelled bands?
- Which control actions will be permitted in autonomous mode, and which must remain manual?
