# API Research and Data Design

Research date: 2026-07-12

Scope: SolaX X1 Hybrid G4, Octopus Flux import/export tariffs, and weather data needed for monitoring, recommendations, simulation, and future autonomous control.

This is a design document only. It does not define an implementation contract yet.

## Summary Recommendation

Use local SolaX data as the primary operational source, Octopus Energy as the tariff and settlement source, and Open-Meteo as the initial weather source.

- **SolaX operational telemetry**: Prefer local polling through Home Assistant's SolaX integration or a Modbus-based integration. Do not depend on SolaX Cloud for production control loops unless its retention, rate limits, and write-control semantics are validated against the actual account and dongle.
- **Octopus tariff and consumption data**: Use the official Octopus REST API. It provides authenticated account and consumption endpoints, unauthenticated product and tariff endpoints, and explicit support for import and export meter points.
- **Weather and solar forecast inputs**: Start with Open-Meteo because it provides forecast, historical archive, UK Met Office model support, solar radiation variables, and a simple HTTP API.
- **PostgreSQL**: Store all time-series data in append-friendly interval tables with UTC timestamps, source metadata, and clear separation between raw readings, normalised intervals, tariff rates, forecasts, and derived optimisation outputs.

## Source Systems

### Home Assistant

Home Assistant should be treated as the integration and control boundary, not the primary analytical database.

Useful data:

- Entity state snapshots from SolaX, sensors, helpers, and automations.
- Energy dashboard-compatible sensors for grid import, grid export, and solar production.
- Future control actions, manual overrides, and safety state.

Historical availability:

- Home Assistant's own recorder retention depends on local configuration and database maintenance.
- For this platform, Home Assistant history should be considered operational convenience only.
- The platform should persist its own PostgreSQL history from the moment collectors are enabled.

Expected polling:

- Let Home Assistant integrations use their own safe update intervals.
- If collecting from Home Assistant's API, poll state snapshots every 10-60 seconds for live dashboard values.
- For long-term energy accounting, persist interval aggregates at 1-minute, 5-minute, and 30-minute resolutions rather than every transient state change.

Authentication:

- Home Assistant REST/WebSocket APIs use long-lived access tokens.
- Store tokens outside git, in environment variables or a secrets manager.

Limitations:

- Entity names and units can change when integrations are reconfigured.
- Recorder history is not a substitute for an analytical data model.
- Control through Home Assistant must be guarded by explicit safety limits and manual override.

Sources:

- Home Assistant SolaX integration: https://www.home-assistant.io/integrations/solax/
- Home Assistant SolaX source: https://github.com/home-assistant/core/tree/dev/homeassistant/components/solax

### SolaX X1 Hybrid G4

There are three practical integration paths.

#### Option A: Home Assistant SolaX Power local REST integration

Home Assistant's built-in SolaX Power integration connects to SolaX inverters on the local network and is classified as local polling. The integration documentation says it retrieves photovoltaic production, battery level and power, and grid feed-in values. The current Home Assistant integration source sets a 30-second scan interval.

Historical data:

- Local REST is a current-state interface, not a historical archive.
- Historical availability begins when Home Assistant or this platform starts recording.

Expected polling:

- 30 seconds is the Home Assistant default.
- For the platform database, store current-state samples at 30 seconds or aggregate to 1 minute unless higher resolution is needed for debugging.

Authentication:

- Local inverter/dongle IP address, port, and password.
- Keep the device on a trusted LAN or VLAN. Do not expose it externally.

Limitations:

- Local API support depends on dongle/inverter firmware.
- It is primarily read-oriented in Home Assistant's built-in integration.
- Entity coverage may be narrower than Modbus for detailed battery and inverter controls.

#### Option B: SolaX Modbus via RS485/TCP

The community Home Assistant SolaX Modbus integration supports SolaX Gen4 Hybrid and Retrofit models and supports Modbus over RS485 and TCP. Its documentation notes that Modbus is normally designed for a single master, and multiple clients can collide unless a multiplexer is used.

Historical data:

- Modbus is current-register access, not a historical archive.
- Historical availability begins when the platform records it.

Expected polling:

- 5-30 seconds for operational telemetry, depending on register volume and adapter reliability.
- 10-30 seconds is a sensible starting point for production monitoring.
- Avoid multiple independent pollers against the inverter. Use one collector or a Modbus proxy/multiplexer.

Authentication:

- Usually network or serial access rather than application-layer auth.
- Protect with network segmentation, firewalling, and adapter credentials where available.

Limitations:

- Register maps vary by model, generation, firmware, and dongle/adapter.
- Some write controls require unlock states and careful sequencing.
- Modbus has weak native security and should be treated as trusted-LAN-only.
- Multiple masters can cause blocked connections or data collisions.

Sources:

- Home Assistant SolaX Modbus docs: https://homeassistant-solax-modbus.readthedocs.io/en/latest/
- SolaX FAQ: https://homeassistant-solax-modbus.readthedocs.io/en/latest/solax-faq/
- Compatible adapters and Modbus cautions: https://homeassistant-solax-modbus.readthedocs.io/en/latest/compatible-adaptors/

#### Option C: SolaX Cloud API

SolaX Cloud can be useful for cross-checking daily totals or recovering data when local collection was down, but the public documentation is harder to verify than the local/Home Assistant paths.

Historical data:

- Treat cloud history as account/device-dependent until validated.
- Expected available history is likely bounded by the device registration date, cloud retention policy, logger upload cadence, and account permissions.
- Do not assume complete high-resolution backfill for production analytics.

Expected polling:

- Poll no faster than every 5 minutes unless the official account documentation confirms a higher limit.
- Prefer hourly or daily reconciliation jobs for cloud data.

Authentication:

- SolaX Cloud token/account credentials, depending on the endpoint generation.
- Store credentials outside git.

Limitations:

- Public endpoint documentation and rate-limit guarantees are not as clear as Octopus or Open-Meteo.
- Cloud readings may be delayed or downsampled compared with local polling.
- Cloud access should not be part of an autonomous control loop.

Design position:

- Use local SolaX polling as the source of truth for live operation.
- Use SolaX Cloud only as an optional reconciliation/backfill source after validating actual account behaviour.

### Octopus Flux Tariff and Meter Data

Octopus exposes an official REST API.

Relevant product codes observed from the public product endpoint on 2026-07-12:

- `FLUX-IMPORT-23-02-14`: Octopus Flux Import, direction `IMPORT`, available from 2023-02-14.
- `FLUX-EXPORT-23-02-14`: Octopus Flux Export, direction `EXPORT`, available from 2023-02-14.
- `INTELLI-FLUX-IMPORT-23-07-14`: Intelligent Octopus Flux Import, direction `IMPORT`, available from 2023-07-13.

Historical data:

- Account endpoint returns current and previous tariff agreements, MPANs/MPRNs, meter serials, and whether an electricity meter point is export.
- Consumption endpoints return half-hourly consumption for electricity and gas meters.
- Export MPAN data uses the same consumption endpoint shape; the value represents exported energy.
- Tariff price endpoints expose unit-rate and standing-charge history for a product/tariff code.
- Flux import/export price history should be retrievable back to the product availability date, subject to the specific regional tariff code and endpoint pagination.
- Customer consumption history should be treated as "available for periods held by Octopus for that account/meter", not as a guaranteed unlimited archive. Backfill should query from move-in or meter install date and handle gaps.

Expected polling:

- Account metadata: daily, and on demand after tariff or meter changes.
- Tariff unit rates and standing charges: daily, plus on service startup. Flux is time-of-use but not like Agile's daily half-hourly wholesale update; still, treat rates as data and refresh regularly.
- Consumption/import/export meter data: every 6-12 hours for backfill, then daily reconciliation. Smart meter data often arrives after a delay, so collectors must re-query recent days.
- For optimisation decisions, use tariff rates directly from the product/tariff endpoints and live SolaX data rather than waiting for Octopus settlement data.

Authentication:

- Product and price endpoints do not require authentication.
- Account and consumption endpoints require the Octopus API key.
- Use HTTP basic auth with API key as username and blank password, or the supported client-library equivalent.

Limitations:

- Pagination defaults to 100 records.
- Use `period_from`, `period_to`, and UTC timestamps ending in `Z` to avoid daylight-saving ambiguity.
- Consumption results can be grouped by day/week/month/quarter, but raw electricity data is half-hourly.
- Price endpoints differ from consumption endpoints: prices return records exactly within the requested period, while consumption returns overlapping intervals.
- Electricity consumption values are returned to 0.001 kWh; billing rounds to 0.01 kWh.
- Regional tariff suffixes matter. The account endpoint should be the source of the actual tariff codes for the property.

Sources:

- Octopus REST endpoint guide: https://docs.octopus.energy/rest/guides/endpoints/
- Octopus products API: https://api.octopus.energy/v1/products/

### Weather and Solar Forecast Data

Open-Meteo is a strong initial choice for weather, solar radiation, and historical reanalysis data.

Useful data:

- Hourly forecast: temperature, cloud cover, precipitation, wind, shortwave radiation, direct radiation, diffuse radiation, direct normal irradiance, and global tilted irradiance.
- 15-minute data may be available, but UK/Europe behaviour depends on model coverage and interpolation.
- Historical archive: weather reanalysis for simulation and model training.
- UK Met Office models are available through Open-Meteo forecast model selection.

Historical availability:

- Open-Meteo historical weather API provides data back to 1940.
- ERA5 is available from 1940 to present with daily updates and a delay.
- ERA5-Land is available from 1950 to present.
- ECMWF IFS high-resolution historical data is available from 2017 to present with no delay.

Expected polling:

- Forecast data: every 1-3 hours for dashboard and recommendation use.
- UK Met Office model data updates hourly according to Open-Meteo's model table.
- Historical archive: batch backfill, then daily catch-up for delayed reanalysis.

Authentication:

- Free/open-access API generally does not require an API key for prototyping.
- Commercial production use requires a paid plan, customer endpoint, and API key.

Limitations:

- Free tier is non-commercial, rate-limited, and has no uptime guarantee.
- Open-Meteo free limits shown on pricing page: 600 calls/minute, 5,000 calls/hour, 10,000 calls/day, and 300,000 calls/month.
- Requests covering many variables or long periods may count as multiple calls.
- Forecasts are model outputs, not measurements; store model name and retrieval time.

Sources:

- Open-Meteo forecast API: https://open-meteo.com/en/docs
- Open-Meteo historical weather API: https://open-meteo.com/en/docs/historical-weather-api
- Open-Meteo pricing and limits: https://open-meteo.com/en/pricing

## Data Freshness Model

| Source | Data type | Native granularity | Recommended collection | Backfill strategy |
| --- | --- | --- | --- | --- |
| SolaX local REST | Live inverter snapshot | Current state | 30 seconds | None; starts at collection time |
| SolaX Modbus | Live registers | Current state | 10-30 seconds | None; starts at collection time |
| SolaX Cloud | Cloud summaries/history | Account-dependent | 1 hour to 1 day | Validate per account before use |
| Octopus account | Meter/tariff metadata | Event-like | Daily | Full account snapshot on setup |
| Octopus consumption | Import/export meter readings | 30 minutes | 6-12 hours, with daily reconciliation | Query from account start/meter install in pages |
| Octopus tariff rates | Unit rates and standing charges | Tariff-dependent intervals | Daily and startup | Query from product availability/account agreement start |
| Open-Meteo forecast | Weather forecast | Hourly, sometimes 15 minutes | 1-3 hours | Store every retrieved forecast run |
| Open-Meteo archive | Historical weather | Hourly | Daily catch-up | Batch from desired simulation start date |

## PostgreSQL Schema Recommendation

Use UTC `timestamptz` for all interval boundaries. Keep local timezone only as metadata for display and tariff interpretation. Store values in consistent SI-style units where practical: W, kW, Wh, kWh, GBP, p/kWh, percent.

Recommended extensions:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;
```

Optional if TimescaleDB is introduced later:

```sql
-- Convert high-volume interval tables to hypertables after the base schema settles.
```

### Reference Tables

```sql
CREATE TABLE sites (
  site_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  timezone text NOT NULL DEFAULT 'Europe/London',
  latitude numeric(9,6),
  longitude numeric(9,6),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE devices (
  device_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id uuid NOT NULL REFERENCES sites(site_id),
  manufacturer text NOT NULL,
  model text NOT NULL,
  serial_number text,
  device_role text NOT NULL,
  source_system text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (site_id, manufacturer, serial_number)
);

CREATE TABLE data_sources (
  source_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL UNIQUE,
  source_type text NOT NULL,
  base_url text,
  poll_interval_seconds integer,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
```

### Energy Telemetry

Use a narrow interval table for measured or calculated energy/power metrics. This keeps ingestion flexible as SolaX/Home Assistant entities are refined.

```sql
CREATE TABLE energy_metrics (
  metric_id bigserial PRIMARY KEY,
  site_id uuid NOT NULL REFERENCES sites(site_id),
  device_id uuid REFERENCES devices(device_id),
  source_id uuid NOT NULL REFERENCES data_sources(source_id),
  metric_name text NOT NULL,
  metric_kind text NOT NULL,
  unit text NOT NULL,
  value numeric NOT NULL,
  interval_start timestamptz NOT NULL,
  interval_end timestamptz,
  observed_at timestamptz NOT NULL DEFAULT now(),
  quality text NOT NULL DEFAULT 'observed',
  raw_payload jsonb,
  CHECK (interval_end IS NULL OR interval_end > interval_start),
  UNIQUE (site_id, source_id, metric_name, interval_start, COALESCE(interval_end, interval_start))
);

CREATE INDEX energy_metrics_lookup_idx
  ON energy_metrics (site_id, metric_name, interval_start DESC);
```

Suggested `metric_name` values:

- `pv_power_w`
- `pv_energy_kwh`
- `load_power_w`
- `grid_import_power_w`
- `grid_export_power_w`
- `grid_import_energy_kwh`
- `grid_export_energy_kwh`
- `battery_soc_percent`
- `battery_charge_power_w`
- `battery_discharge_power_w`
- `battery_charge_energy_kwh`
- `battery_discharge_energy_kwh`
- `inverter_temperature_c`

### Meter Readings

Octopus import/export readings are settlement-grade half-hour intervals and should be kept separately from live inverter telemetry.

```sql
CREATE TABLE meter_points (
  meter_point_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id uuid NOT NULL REFERENCES sites(site_id),
  direction text NOT NULL CHECK (direction IN ('import', 'export', 'gas')),
  mpan_mprn text NOT NULL,
  meter_serial_number text NOT NULL,
  is_active boolean NOT NULL DEFAULT true,
  metadata jsonb NOT NULL DEFAULT '{}',
  UNIQUE (mpan_mprn, meter_serial_number)
);

CREATE TABLE meter_consumption_intervals (
  meter_point_id uuid NOT NULL REFERENCES meter_points(meter_point_id),
  interval_start timestamptz NOT NULL,
  interval_end timestamptz NOT NULL,
  consumption_kwh numeric(12,6) NOT NULL,
  source_id uuid NOT NULL REFERENCES data_sources(source_id),
  retrieved_at timestamptz NOT NULL DEFAULT now(),
  raw_payload jsonb,
  PRIMARY KEY (meter_point_id, interval_start, interval_end)
);

CREATE INDEX meter_consumption_time_idx
  ON meter_consumption_intervals (interval_start DESC);
```

### Tariffs and Prices

Represent import and export tariffs independently. Store regional tariff codes from the account endpoint, not only product codes.

```sql
CREATE TABLE tariff_products (
  product_code text PRIMARY KEY,
  display_name text NOT NULL,
  full_name text,
  direction text CHECK (direction IN ('IMPORT', 'EXPORT')),
  brand text,
  available_from timestamptz,
  available_to timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE tariff_agreements (
  agreement_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id uuid NOT NULL REFERENCES sites(site_id),
  meter_point_id uuid REFERENCES meter_points(meter_point_id),
  product_code text REFERENCES tariff_products(product_code),
  tariff_code text NOT NULL,
  direction text NOT NULL CHECK (direction IN ('import', 'export')),
  valid_from timestamptz NOT NULL,
  valid_to timestamptz,
  source_id uuid NOT NULL REFERENCES data_sources(source_id),
  raw_payload jsonb,
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE tariff_rate_intervals (
  tariff_code text NOT NULL,
  rate_type text NOT NULL,
  payment_method text,
  valid_from timestamptz NOT NULL,
  valid_to timestamptz NOT NULL,
  value_exc_vat numeric(12,6),
  value_inc_vat numeric(12,6) NOT NULL,
  unit text NOT NULL,
  source_id uuid NOT NULL REFERENCES data_sources(source_id),
  retrieved_at timestamptz NOT NULL DEFAULT now(),
  raw_payload jsonb,
  PRIMARY KEY (tariff_code, rate_type, valid_from, valid_to, COALESCE(payment_method, ''))
);

CREATE INDEX tariff_rate_lookup_idx
  ON tariff_rate_intervals (tariff_code, rate_type, valid_from DESC);
```

Suggested `rate_type` values:

- `standard_unit_rate`
- `day_unit_rate`
- `night_unit_rate`
- `standing_charge`
- `export_unit_rate`

### Weather Forecasts and History

Store forecasts as forecast runs plus interval values. This allows simulation to distinguish "what we knew at the time" from later actual/reanalysis data.

```sql
CREATE TABLE weather_locations (
  weather_location_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id uuid NOT NULL REFERENCES sites(site_id),
  latitude numeric(9,6) NOT NULL,
  longitude numeric(9,6) NOT NULL,
  elevation_m numeric,
  timezone text NOT NULL DEFAULT 'Europe/London',
  UNIQUE (site_id, latitude, longitude)
);

CREATE TABLE weather_runs (
  weather_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  weather_location_id uuid NOT NULL REFERENCES weather_locations(weather_location_id),
  source_id uuid NOT NULL REFERENCES data_sources(source_id),
  run_type text NOT NULL CHECK (run_type IN ('forecast', 'historical', 'historical_forecast')),
  model text,
  retrieved_at timestamptz NOT NULL DEFAULT now(),
  forecast_reference_time timestamptz,
  raw_request jsonb,
  raw_response_metadata jsonb
);

CREATE TABLE weather_interval_values (
  weather_run_id uuid NOT NULL REFERENCES weather_runs(weather_run_id),
  interval_start timestamptz NOT NULL,
  interval_end timestamptz NOT NULL,
  variable_name text NOT NULL,
  unit text NOT NULL,
  value numeric,
  PRIMARY KEY (weather_run_id, interval_start, interval_end, variable_name)
);

CREATE INDEX weather_values_lookup_idx
  ON weather_interval_values (variable_name, interval_start DESC);
```

Suggested weather variables:

- `temperature_2m_c`
- `relative_humidity_2m_percent`
- `cloud_cover_percent`
- `shortwave_radiation_w_m2`
- `direct_radiation_w_m2`
- `diffuse_radiation_w_m2`
- `direct_normal_irradiance_w_m2`
- `global_tilted_irradiance_w_m2`
- `precipitation_mm`
- `wind_speed_10m_ms`

### Optimisation and Audit Tables

These tables are not needed for initial ingestion, but the data model should leave room for them.

```sql
CREATE TABLE optimisation_runs (
  optimisation_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id uuid NOT NULL REFERENCES sites(site_id),
  mode text NOT NULL CHECK (mode IN ('monitoring', 'recommendation', 'simulation', 'autonomous')),
  horizon_start timestamptz NOT NULL,
  horizon_end timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  model_version text,
  input_refs jsonb NOT NULL DEFAULT '{}',
  objective_summary jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'created'
);

CREATE TABLE optimisation_actions (
  action_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  optimisation_run_id uuid NOT NULL REFERENCES optimisation_runs(optimisation_run_id),
  action_type text NOT NULL,
  target_device_id uuid REFERENCES devices(device_id),
  scheduled_for timestamptz NOT NULL,
  parameters jsonb NOT NULL DEFAULT '{}',
  expected_impact jsonb NOT NULL DEFAULT '{}',
  safety_constraints jsonb NOT NULL DEFAULT '{}',
  execution_status text NOT NULL DEFAULT 'proposed',
  executed_at timestamptz,
  audit_message text
);
```

## Initial Collection Priorities

1. **Identity and metadata**
   - Site, timezone, location.
   - SolaX inverter model, serial, firmware if available.
   - Octopus account number, MPANs, export MPAN, meter serials, actual tariff codes.

2. **Core live telemetry**
   - PV generation power.
   - House/load power.
   - Grid import/export power.
   - Battery SoC, charge power, discharge power.
   - Daily cumulative energy counters where available.

3. **Settlement intervals**
   - Octopus import half-hour readings.
   - Octopus export half-hour readings.
   - Tariff unit rates and standing charges for import/export.

4. **Forecast inputs**
   - Hourly solar radiation and cloud cover.
   - Hourly temperature and precipitation.
   - Store forecast runs, not just latest values.

5. **Simulation-ready history**
   - Backfill Octopus consumption/export intervals.
   - Backfill Open-Meteo historical weather from the chosen simulation start date.
   - Keep SolaX local telemetry from first collector startup onward.

## Open Questions for Validation

- Exact SolaX X1 Hybrid G4 communication hardware: PocketWiFi 3.0, PocketLAN, built-in LAN, RS485 adapter, or another dongle.
- Whether local SolaX REST exposes all required sensors for this specific inverter/firmware.
- Whether Modbus write controls are required, and which safety lock/unlock behaviour applies to this inverter.
- Actual Octopus import and export MPANs and regional tariff codes from the authenticated account endpoint.
- Smart meter data delay pattern for this account.
- Site latitude/longitude, panel tilt, panel azimuth, inverter/battery capacity, and export limit.
- Whether production use of Open-Meteo requires a paid commercial licence for this project.

## Design Decisions to Review

- Prefer local SolaX polling over SolaX Cloud for operational telemetry.
- Store Octopus meter data separately from inverter telemetry.
- Store weather forecasts by retrieval run to support forecast-verification and simulation.
- Use narrow metric tables initially; introduce typed aggregate/materialized views once entity coverage stabilises.
- Keep autonomous control audit tables in the schema plan from the beginning, even before any control implementation.
