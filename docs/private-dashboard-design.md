# Private Local Dashboard Design

This document defines a future private Wattson dashboard. It is not implemented yet.

## Purpose

The private dashboard is the place for operational, detailed and live household energy data. It is separate from the public GitHub Pages summary, which publishes only monthly aggregate information.

## Deployment Model

- Runs locally on the Raspberry Pi or a trusted home-network host.
- Binds to the local network by default, not the public internet.
- Requires authentication before showing data.
- Uses local databases and ignored operational storage.
- Never publishes detailed household activity to GitHub Pages.

## Initial Views

### Live Energy

- Current SolaX telemetry.
- PV power.
- Battery state of charge.
- Battery charge/discharge power.
- Grid import/export power.
- Inverter output.
- Collector freshness and data-quality status.

### Battery and Grid Flows

- Battery direction and power over time.
- Grid import/export over time.
- Self-consumption and grid-independence trends.
- Daily and intraday flow charts.

### Octopus Tariffs

- Current import/export rates.
- Upcoming rates where available.
- Active import/export agreements.
- Tariff refresh status.

### Historical Analytics

- Generation, import, export and consumption charts.
- Financial benefit by month and year.
- Battery throughput and utilisation.
- Data coverage and validation gaps.

### Forecasts

- Weather forecast inputs.
- Solar generation forecast.
- Tariff-aware forecast windows.

### Recommendations and What-If Analysis

- Suggested operating windows.
- Savings estimates.
- Scenario comparison.
- Confidence and data-quality assumptions.

### Collector Health

- Last successful SolaX collection.
- Last successful Octopus refresh.
- Snapshot generation status.
- Data-quality events.
- Failed collector runs.

## Security Requirements

- Authentication is mandatory.
- No anonymous public access.
- No raw credentials in logs, pages or generated artifacts.
- No inverter write or battery-control capability unless a separate guarded-control design is approved later.
- Public GitHub Pages output remains monthly aggregate only.
