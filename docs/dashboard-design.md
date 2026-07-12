# Python Dash Dashboard Design

Research/design date: 2026-07-12

Scope: a Python Dash web application that reads from PostgreSQL and displays historical energy and financial performance for the home energy optimisation platform.

This is a design document only. Do not implement the dashboard until the data contracts, tariff assumptions, and entity mapping have been confirmed.

## Architecture Component

The dashboard should be a separate application component in the production architecture.

```text
Home Assistant -> Data collection -> PostgreSQL -> Dashboard
                                  -> Optimisation engine
                                  -> Testing and validation
                                  -> Documentation
```

Responsibilities:

- Query curated PostgreSQL tables and views for historical energy, tariff, weather, and optimisation data.
- Display trusted historical performance before any recommendation or autonomous-control feature is exposed.
- Make data quality visible so incomplete or stale inputs are not mistaken for real performance.
- Keep write access disabled by default. Early dashboard versions should be read-only.

Non-responsibilities:

- Polling SolaX, Octopus, Home Assistant, or weather APIs directly.
- Running optimisation decisions inside Dash request handlers.
- Writing control commands to Home Assistant.
- Acting as the canonical source of financial calculations without tested database views or service-layer functions behind it.

## Application Goals

- Provide a clear daily, weekly, monthly, and custom-period view of energy behaviour.
- Quantify financial performance using Octopus import and export rates.
- Explain battery usage, cycling, and effectiveness.
- Highlight missing, stale, inconsistent, or delayed data.
- Prepare the interface shape for later simulation and what-if analysis.

## Initial Pages

### Overview

Purpose: a high-level operational and financial summary for the selected period.

Required metrics:

- Total PV generation, kWh.
- Total household load, kWh.
- Grid import, kWh.
- Grid export, kWh.
- Battery charge energy, kWh.
- Battery discharge energy, kWh.
- Self-consumption, percent.
- Grid independence, percent.
- Avoided import cost, GBP.
- Export income, GBP.
- Total financial benefit, GBP.
- Data completeness, percent.

Charts:

- KPI cards for energy, battery, and financial headline values.
- Daily stacked energy bars: PV used on site, battery discharge, grid import, export.
- Period cost/benefit line or bar chart by day.
- Current data quality status strip.

Underlying data fields:

- `energy_metrics.metric_name`
- `energy_metrics.value`
- `energy_metrics.unit`
- `energy_metrics.interval_start`
- `energy_metrics.interval_end`
- `energy_metrics.quality`
- `meter_consumption_intervals.consumption_kwh`
- `meter_points.direction`
- `tariff_rate_intervals.rate_type`
- `tariff_rate_intervals.value_inc_vat`
- `tariff_rate_intervals.valid_from`
- `tariff_rate_intervals.valid_to`

Preferred derived views:

- `v_energy_daily_summary`
- `v_financial_daily_summary`
- `v_data_quality_daily_summary`

### Energy Flows

Purpose: show how energy moved between PV, house load, battery, grid import, and grid export.

Required metrics:

- PV generation by interval.
- House load by interval.
- Grid import by interval.
- Grid export by interval.
- Battery charge by interval.
- Battery discharge by interval.
- Net grid position by interval.
- PV-to-load estimate.
- PV-to-battery estimate.
- Battery-to-load estimate.
- Exported surplus estimate.

Charts:

- Time-series line chart for power: PV, load, import, export, battery charge, battery discharge.
- Stacked interval energy chart by day or half-hour.
- Sankey diagram for selected period energy flow.
- Heatmap by hour of day and date for import/export intensity.

Underlying data fields:

- `energy_metrics.metric_name IN ('pv_power_w', 'load_power_w', 'grid_import_power_w', 'grid_export_power_w', 'battery_charge_power_w', 'battery_discharge_power_w')`
- `energy_metrics.metric_name IN ('pv_energy_kwh', 'grid_import_energy_kwh', 'grid_export_energy_kwh', 'battery_charge_energy_kwh', 'battery_discharge_energy_kwh')`
- `energy_metrics.interval_start`
- `energy_metrics.interval_end`
- `energy_metrics.value`
- `meter_consumption_intervals.consumption_kwh`
- `meter_points.direction`

Preferred derived views:

- `v_energy_interval_balances`
- `v_energy_daily_flows`

Notes:

- Flow attribution must be explicit. If only aggregate import/export/PV/battery values are available, PV-to-load and battery-to-load are estimates, not directly measured values.

### Financial Savings

Purpose: show the financial value produced by solar, battery, export, and tariff-aware behaviour.

Required metrics:

- Import cost, GBP.
- Export income, GBP.
- Avoided import cost, GBP.
- Total financial benefit, GBP.
- Average import price, p/kWh.
- Average export price, p/kWh.
- Peak/off-peak import cost split, if tariff intervals support it.
- Battery arbitrage estimate, GBP, once battery source attribution is trusted.
- Baseline comparison cost, GBP.

Charts:

- Daily financial benefit bar chart split into avoided import cost and export income.
- Import cost versus export income time series.
- Tariff rate timeline for import and export.
- Cumulative benefit over selected period.
- Baseline cost versus actual net cost comparison.

Underlying data fields:

- `meter_consumption_intervals.consumption_kwh`
- `meter_points.direction`
- `tariff_agreements.tariff_code`
- `tariff_agreements.direction`
- `tariff_rate_intervals.rate_type`
- `tariff_rate_intervals.value_inc_vat`
- `tariff_rate_intervals.unit`
- `tariff_rate_intervals.valid_from`
- `tariff_rate_intervals.valid_to`
- `energy_metrics.metric_name IN ('pv_energy_kwh', 'grid_import_energy_kwh', 'grid_export_energy_kwh', 'battery_charge_energy_kwh', 'battery_discharge_energy_kwh')`

Preferred derived views:

- `v_tariff_rate_intervals_normalised`
- `v_meter_consumption_costed`
- `v_financial_interval_summary`
- `v_financial_daily_summary`

Notes:

- Standing charges should be shown separately from optimisation benefit because they are usually not avoidable by PV or battery behaviour.
- VAT treatment must be consistent across import, export, and baseline calculations.

### Battery Performance

Purpose: assess how the battery is being used and whether that use is economically and operationally healthy.

Required metrics:

- Average, minimum, and maximum state of charge, percent.
- Battery charge energy, kWh.
- Battery discharge energy, kWh.
- Battery throughput, kWh.
- Estimated round-trip efficiency, percent, if charge/discharge counters are trustworthy.
- Equivalent full cycles, count.
- Time at low SoC, hours.
- Time at high SoC, hours.
- Peak charge power, kW.
- Peak discharge power, kW.
- Battery contribution to avoided import, kWh and GBP, once attribution is trusted.

Charts:

- SoC time-series line chart.
- Charge/discharge power area chart.
- Daily throughput and equivalent cycles bar chart.
- SoC distribution histogram.
- Battery operation over tariff bands heatmap.

Underlying data fields:

- `energy_metrics.metric_name = 'battery_soc_percent'`
- `energy_metrics.metric_name = 'battery_charge_power_w'`
- `energy_metrics.metric_name = 'battery_discharge_power_w'`
- `energy_metrics.metric_name = 'battery_charge_energy_kwh'`
- `energy_metrics.metric_name = 'battery_discharge_energy_kwh'`
- `energy_metrics.interval_start`
- `energy_metrics.interval_end`
- `energy_metrics.value`
- `devices.metadata`, especially usable battery capacity and nominal capacity.
- `tariff_rate_intervals.value_inc_vat`
- `tariff_rate_intervals.valid_from`
- `tariff_rate_intervals.valid_to`

Preferred derived views:

- `v_battery_interval_summary`
- `v_battery_daily_summary`

### Data Quality

Purpose: make trustworthiness visible before performance numbers are used for decisions.

Required metrics:

- Completeness by source, percent.
- Missing intervals count.
- Stale data duration.
- Duplicate interval count.
- Late-arriving Octopus intervals count.
- Negative or impossible values count.
- Unit mismatch count.
- Energy balance residual, kWh and percent.
- Last successful poll by source.
- Last ingestion error by source.

Charts:

- Source health table.
- Calendar heatmap of missing or partial days.
- Completeness trend by day.
- Energy balance residual time series.
- Ingestion latency histogram for Octopus consumption.

Underlying data fields:

- `energy_metrics.source_id`
- `energy_metrics.metric_name`
- `energy_metrics.interval_start`
- `energy_metrics.interval_end`
- `energy_metrics.observed_at`
- `energy_metrics.quality`
- `energy_metrics.raw_payload`
- `meter_consumption_intervals.retrieved_at`
- `meter_consumption_intervals.interval_start`
- `meter_consumption_intervals.interval_end`
- `weather_runs.retrieved_at`
- `weather_interval_values.interval_start`
- `weather_interval_values.interval_end`
- `data_sources.name`
- `data_sources.poll_interval_seconds`

Preferred derived views:

- `v_source_freshness`
- `v_interval_completeness`
- `v_energy_balance_quality`
- `v_ingestion_latency`

### Future What-If Analysis

Purpose: provide the page shape for scenario analysis before autonomous control exists.

Required metrics:

- Forecast PV generation, kWh.
- Forecast household load, kWh.
- Forecast grid import/export under baseline, kWh.
- Forecast grid import/export under scenario, kWh.
- Forecast battery SoC path.
- Forecast import cost, export income, and total benefit.
- Scenario delta versus baseline, GBP and kWh.
- Constraint violations, count and severity.

Charts:

- Forecast generation/load/import/export time series.
- Battery SoC scenario comparison.
- Scenario financial comparison bars.
- Tariff and recommended battery action timeline.
- Sensitivity chart for selected assumptions such as battery reserve, export price, or load forecast error.

Underlying data fields:

- `weather_runs.run_type = 'forecast'`
- `weather_interval_values.variable_name`
- `weather_interval_values.value`
- `tariff_rate_intervals.value_inc_vat`
- `tariff_rate_intervals.valid_from`
- `tariff_rate_intervals.valid_to`
- `optimisation_runs.mode IN ('recommendation', 'simulation')`
- `optimisation_runs.horizon_start`
- `optimisation_runs.horizon_end`
- `optimisation_runs.input_refs`
- `optimisation_runs.objective_summary`
- `optimisation_actions.action_type`
- `optimisation_actions.scheduled_for`
- `optimisation_actions.parameters`
- `optimisation_actions.expected_impact`
- `optimisation_actions.safety_constraints`

Preferred derived views:

- `v_latest_weather_forecast`
- `v_simulation_scenario_summary`
- `v_optimisation_action_timeline`

Notes:

- This page should remain read-only until the recommendation and simulation phases have proven reliable.
- It should visibly distinguish historical measured data from forecast and simulated data.

## Metric Calculations

All calculations should use UTC interval joins internally, then render dates and tariff bands in `Europe/London`. Financial values should use GBP and tariff rates including VAT unless explicitly labelled otherwise.

### Avoided Import Cost

Definition: the estimated cost that would have been paid if on-site consumed solar and battery-discharge energy had instead been imported from the grid at the applicable import tariff.

Preferred interval formula:

```text
avoided_import_cost_gbp =
  sum(self_consumed_generation_kwh_i * import_unit_rate_gbp_per_kwh_i)
```

Where:

```text
self_consumed_generation_kwh_i =
  max(0, pv_generation_kwh_i - grid_export_kwh_i)
```

If battery attribution is trusted:

```text
self_consumed_generation_kwh_i =
  pv_direct_to_load_kwh_i + battery_discharge_to_load_from_pv_kwh_i
```

Important exclusions:

- Do not include exported energy in avoided import cost.
- Do not include standing charges.
- Do not double-count battery discharge if it was charged from grid import.

Required fields:

- `energy_metrics.pv_energy_kwh`
- `energy_metrics.grid_export_energy_kwh` or export meter intervals.
- `tariff_rate_intervals.value_inc_vat` for import unit rates.
- Optional attribution fields from future flow model.

### Export Income

Definition: revenue earned from exported electricity.

Formula:

```text
export_income_gbp =
  sum(grid_export_kwh_i * export_unit_rate_gbp_per_kwh_i)
```

Required fields:

- `meter_consumption_intervals.consumption_kwh` where `meter_points.direction = 'export'`, preferred for settled values.
- `energy_metrics.grid_export_energy_kwh`, acceptable for live estimates.
- `tariff_rate_intervals.value_inc_vat` for export unit rates.

Notes:

- Use Octopus export meter data for historical financial reporting when available.
- Use inverter export telemetry only for provisional same-day estimates.

### Total Financial Benefit

Definition: total value attributable to solar and battery operation for the period.

Initial formula:

```text
total_financial_benefit_gbp =
  avoided_import_cost_gbp + export_income_gbp
```

Optional later formula for optimisation reporting:

```text
total_financial_benefit_gbp =
  baseline_import_cost_gbp
  - actual_import_cost_gbp
  + export_income_gbp
  - additional_operating_costs_gbp
```

Where `additional_operating_costs_gbp` may include battery degradation cost, extra grid charging cost, or other explicit modelled costs.

Important exclusions:

- Standing charge savings should normally be zero and shown separately.
- Export income must not be treated as avoided cost.
- Battery degradation should be excluded until a reviewed degradation model exists.

### Self-Consumption

Definition: the proportion of generated solar energy used on site rather than exported.

Formula:

```text
self_consumption_percent =
  100 * (pv_generation_kwh - grid_export_kwh) / pv_generation_kwh
```

Bounds:

```text
0 <= self_consumption_percent <= 100
```

If `pv_generation_kwh = 0`, show `null` or `not applicable`, not `0%`.

Required fields:

- `energy_metrics.pv_energy_kwh`
- `meter_consumption_intervals` export values, preferred for settled periods.
- `energy_metrics.grid_export_energy_kwh`, acceptable for live/provisional periods.

Notes:

- If the battery was charged from the grid and later used on site, that discharge is not solar self-consumption.
- If export measurement is delayed, label current-day self-consumption as provisional.

### Grid Independence

Definition: the proportion of household consumption supplied without grid import.

Formula:

```text
grid_independence_percent =
  100 * (1 - grid_import_kwh / household_load_kwh)
```

Equivalent:

```text
grid_independence_percent =
  100 * (household_load_kwh - grid_import_kwh) / household_load_kwh
```

Bounds:

```text
0 <= grid_independence_percent <= 100
```

If `household_load_kwh = 0`, show `null` or `not applicable`.

Required fields:

- `energy_metrics.load_energy_kwh` if available, or derived from power integration.
- `meter_consumption_intervals` import values, preferred for settled periods.
- `energy_metrics.grid_import_energy_kwh`, acceptable for live/provisional periods.

Notes:

- The dashboard must state whether grid import comes from Octopus meter data or inverter telemetry.
- Household load may need to be derived as `pv_generation + grid_import + battery_discharge - grid_export - battery_charge`, depending on available sensors.

### Battery Throughput

Definition: total energy processed by the battery over a period.

Recommended formula for operational wear:

```text
battery_throughput_kwh =
  battery_charge_energy_kwh + battery_discharge_energy_kwh
```

Alternative formula for cycle-equivalent reporting:

```text
equivalent_full_cycles =
  battery_discharge_energy_kwh / usable_battery_capacity_kwh
```

or, if using total throughput:

```text
equivalent_full_cycles =
  battery_throughput_kwh / (2 * usable_battery_capacity_kwh)
```

Required fields:

- `energy_metrics.battery_charge_energy_kwh`
- `energy_metrics.battery_discharge_energy_kwh`
- `devices.metadata.usable_battery_capacity_kwh`

Notes:

- Charge and discharge counters must be checked for reset behaviour.
- Throughput is not the same as useful delivered energy.
- Battery throughput should not be converted into degradation cost until the battery chemistry, warranty terms, and cycle model are confirmed.

## Data Access Design

The Dash app should query database views or read-only service functions instead of embedding complex SQL in callbacks.

Suggested layers:

- **Database views**: stable metric and chart contracts.
- **Repository layer**: typed query functions with date range, site, and resolution parameters.
- **Calculation layer**: only lightweight formatting or final aggregation in Python; canonical formulas should live in tested SQL views or shared service functions.
- **Dash callbacks**: UI state, filtering, chart rendering, and error presentation.

Initial filters:

- Site.
- Date range.
- Resolution: half-hour, hour, day, week, month.
- Data source preference: settled, provisional, or blended.
- Include/exclude current day.

## Proposed Application Folder Structure

This is a proposed future structure only; do not create these files yet.

```text
dashboard/
  Dockerfile
  pyproject.toml
  README.md
  src/
    solax_predbat_dashboard/
      __init__.py
      app.py
      config.py
      logging.py
      db/
        __init__.py
        connection.py
        repositories.py
        queries/
          overview.sql
          energy_flows.sql
          financial_savings.sql
          battery_performance.sql
          data_quality.sql
          what_if.sql
      pages/
        __init__.py
        overview.py
        energy_flows.py
        financial_savings.py
        battery_performance.py
        data_quality.py
        what_if.py
      components/
        __init__.py
        filters.py
        kpi_card.py
        charts.py
        tables.py
        status.py
      calculations/
        __init__.py
        finance.py
        energy.py
        quality.py
      assets/
        styles.css
  tests/
    test_finance_calculations.py
    test_energy_calculations.py
    test_repository_queries.py
    test_page_callbacks.py
```

## Proposed Docker Service

This is a future `docker-compose.yml` service proposal only. It should not be added until PostgreSQL and the dashboard app exist.

```yaml
dashboard:
  build:
    context: ./dashboard
  container_name: solax-predbat-dashboard
  environment:
    DASH_HOST: 0.0.0.0
    DASH_PORT: 8050
    DATABASE_URL: ${DATABASE_URL}
    APP_TIMEZONE: Europe/London
    DASH_DEBUG: "false"
  ports:
    - "8050:8050"
  depends_on:
    postgres:
      condition: service_healthy
  restart: unless-stopped
```

Recommended production notes:

- Use a non-root container user.
- Run behind a reverse proxy before exposing beyond localhost.
- Keep `DATABASE_URL` in `.env` or a secrets manager.
- Use read-only database credentials for the dashboard.
- Add health checks once the app has a `/health` route.

## Assumptions to Confirm Before Trusting Calculations

- The exact Octopus import and export tariff codes for the property are known from the authenticated account endpoint.
- Import and export meter readings are both available and mapped to the correct MPANs and meter serial numbers.
- Octopus export readings represent export energy in kWh, not netted import/export.
- The tariff rate used for each interval matches the meter point direction, region, payment method, and validity window.
- All interval joins use UTC internally and correctly handle UK daylight-saving transitions.
- VAT treatment is agreed: dashboard headline values should use rates including VAT unless labelled otherwise.
- Standing charges are excluded from optimisation benefit and shown separately.
- SolaX PV generation, grid import/export, and battery counters are cumulative or interval values with known reset behaviour.
- Household load can be measured directly or derived with a validated energy-balance formula.
- Battery charge source can be distinguished if grid-charged energy is to be excluded from solar self-consumption.
- Battery usable capacity is known, not only nominal capacity.
- Battery charge/discharge values use AC-side or DC-side measurement consistently.
- Same-day inverter telemetry is labelled provisional until reconciled with Octopus settlement data.
- Missing or stale data intervals are excluded, interpolated, or flagged according to an agreed quality policy.
- Weather and forecast data are not used in historical performance charts unless clearly labelled as forecast or reanalysis.
- Financial benefits are household bill estimates, not legally authoritative billing calculations.

## Review Questions

- Should financial reporting use Octopus settled meter data only, or blend live SolaX estimates for the current day?
- Should avoided import cost include battery discharge only when the battery was charged from PV?
- Should dashboard calculations be implemented as SQL views, Python service functions, or both?
- Should the initial dashboard be local-only, or designed from day one for authenticated remote access?
- Which baseline should total financial benefit compare against: no solar, no battery, no optimisation, or current actual tariff behaviour?
