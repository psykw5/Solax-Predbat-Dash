# Octopus Financial Methodology

This document describes the v0.2 Octopus tariff and financial-benefit calculation method.

## Credentials and Security

Octopus credentials are read only from local `.env` keys:

- `OCTOPUS_API_KEY`
- `OCTOPUS_ACCOUNT_NUMBER`

The pipeline refuses to call the Octopus API unless `.env` is ignored by Git. Credentials are never printed, logged, written to raw data, processed data, or reports.

Account numbers, MPANs, meter serial numbers, addresses and property identifiers are redacted or replaced with stable local pseudonymous identifiers before raw responses are written under `data/raw/octopus/`.

## Source APIs

The account endpoint is used to discover electricity meter points and current or historic tariff agreements. Octopus documents this endpoint at `https://api.octopus.energy/v1/accounts/<account-number>/` and states that it requires API-key authentication.

Historic unit rates are retrieved from electricity tariff unit-rate endpoints of the form:

`https://api.octopus.energy/v1/products/<product-code>/electricity-tariffs/<tariff-code>/<rate-type>/`

The pipeline queries explicit UTC `period_from` and `period_to` values to avoid daylight-saving ambiguity.

## Tariff Discovery

For each electricity meter point:

- `is_export = false` is treated as import.
- `is_export = true` is treated as export.
- Each agreement contributes tariff code, product code, direction, `valid_from`, `valid_to`, source endpoint and ingestion timestamp.

The product code is derived from Octopus electricity tariff codes by removing the leading register prefix and trailing region suffix. For example, `E-1R-PRODUCT-CODE-A` becomes `PRODUCT-CODE`.

## Timezone Logic

SolaX report timestamps are stored as naive local timestamps and are interpreted as `Europe/London`.

The financial transform:

1. Localises SolaX interval start and end timestamps to `Europe/London`.
2. Flags and excludes ambiguous or nonexistent local timestamps around BST/GMT transitions.
3. Floors interval starts to local UK settlement half-hours.
4. Converts settlement boundaries to UTC for tariff-rate joins.

Octopus unit-rate intervals are treated as UTC-aware intervals.

Daily SolaX cumulative baseline rows with no positive-duration interval are preserved as validation events and excluded from financial calculations. No energy is allocated to a zero-minute interval.

## Tariff Selection

Tariff agreements are preserved in the agreement audit dataset even if they have zero duration. Zero-duration agreements are treated as administrative artefacts and excluded from active tariff joins.

Unit rates are first clipped to the positive-duration Octopus agreement window. If Octopus returns multiple payment-method rows for the same active tariff window, the deterministic selection order is:

1. `DIRECT_DEBIT`
2. unknown payment method
3. `NON_DIRECT_DEBIT`

This resolves standard-variable duplicate payment-method rows without treating them as overlapping active tariffs.

## Financial Formulas

For each supported settlement half-hour:

- `estimated self-consumed solar = max(generation - export, 0)`
- `household consumption = estimated self-consumed solar + grid import`
- `avoided import value = estimated self-consumed solar x applicable import unit rate`
- `export income = exported energy x applicable export unit rate`
- `total financial benefit = avoided import value + export income`

Octopus unit rates are pence per kWh including VAT, so values are divided by 100 to produce pounds.

SolaX Plant Reports also include `Daily consumed(kWh)`. Wattson preserves that
source counter as `reported_inverter_consumption_kwh` for diagnostics, but it is
not used as canonical household consumption. The historical data shows it is an
inverter-reported counter that appears to include battery or inverter throughput,
so canonical household consumption is derived from the physical balance between
PV generation, export and grid import.

## Exclusions

The calculation excludes:

- standing charges;
- system purchase or finance costs;
- maintenance;
- battery degradation;
- deemed-export payments;
- settlement periods without supported import or export tariff data.

## Metric Status

- Tariff rates are exact where retrieved directly from Octopus unit-rate endpoints.
- Energy quantities are reconstructed from SolaX cumulative daily report intervals.
- Financial values are exact tariff-rate calculations over reconstructed energy quantities.
- Intervals with missing tariff data are excluded and counted in the lifetime summary.
- `confirmed_financial_benefit` includes only intervals with complete reconstructed energy and import/export tariff coverage.
- `estimated_lifetime_financial_benefit` adds only explicitly supported estimates for genuine tariff retrieval gaps. It does not estimate export income before an export agreement existed.

## Output Datasets

Ignored local outputs:

- `data/processed/octopus/tariff_agreements.parquet`
- `data/processed/octopus/import_unit_rates.parquet`
- `data/processed/octopus/export_unit_rates.parquet`
- `data/processed/financial/half_hourly_financials.parquet`
- `data/processed/financial/monthly_financial_summary.csv`
- `data/processed/financial/annual_financial_summary.csv`
- `data/processed/financial/lifetime_summary.json`
- `data/processed/financial/financial_data_quality_report.csv`
