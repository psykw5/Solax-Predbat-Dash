# Data Quality Rules

This document describes the current SolaX historical-ingestion validation rules. Examples are synthetic and contain no personal data.

## file_read_error

- **Why it exists**: Identifies workbooks that cannot be read as supported SolaX Plant Reports.
- **Severity**: `error`
- **Processing continues**: Yes. Other workbooks continue to process.
- **Downstream treatment**: Treat the affected source file as excluded from metrics until investigated.
- **Synthetic example**: A workbook has only a title sheet and no `Update time` column.

## missing_timestamp

- **Why it exists**: Detects absent five-minute timestamps within a single local day.
- **Severity**: `warning`
- **Processing continues**: Yes.
- **Downstream treatment**: Daily totals may remain usable, but interval charts should show gaps or reduced confidence.
- **Synthetic example**: `00:00`, `00:05`, and `00:15` are present; `00:10` is missing.

## duplicate_interval

- **Why it exists**: Detects repeated timestamps within the same source workbook.
- **Severity**: `warning`
- **Processing continues**: Yes. The duplicate row is not used for interval conversion.
- **Downstream treatment**: Use the first observed interval and surface the duplicate count in data-quality views.
- **Synthetic example**: Two rows both report `2026-01-01 12:00:00`.

## overlapping_interval

- **Why it exists**: Detects the same interval appearing in more than one workbook.
- **Severity**: `warning`
- **Processing continues**: Yes. The canonical dataset keeps one deterministic row for the interval and records an overlap flag.
- **Downstream treatment**: Aggregates can use the canonical row; audits should review overlapping source periods.
- **Synthetic example**: A monthly report and a replacement report both include `2026-01-31 23:55:00`.

## overlapping_reporting_period

- **Why it exists**: Detects workbook-level reporting periods that overlap.
- **Severity**: `warning`
- **Processing continues**: Yes.
- **Downstream treatment**: Expect possible overlapping intervals; prefer canonical interval outputs rather than raw workbook sums.
- **Synthetic example**: One report covers January and another covers `2026-01-25` to `2026-02-24`.

## midnight_reset

- **Why it exists**: Confirms cumulative daily counters reset at the local day boundary.
- **Severity**: `info`
- **Processing continues**: Yes.
- **Downstream treatment**: Expected behaviour. Use as evidence that daily cumulative differencing is appropriate.
- **Synthetic example**: PV yield ends one day at `12.4 kWh` and starts the next day at `0.0 kWh`.

## counter_rollback

- **Why it exists**: Detects cumulative counters decreasing within the same local day.
- **Severity**: `warning`
- **Processing continues**: Yes. The affected interval value is left blank and a quality flag is attached.
- **Downstream treatment**: Do not silently interpolate for financial or dashboard totals. Show provisional status for affected measures.
- **Synthetic example**: Consumption rises to `5.2 kWh` at `12:00`, then drops to `4.9 kWh` at `12:05`.

## daylight_saving_transition

- **Why it exists**: Flags timestamps that are ambiguous or nonexistent in `Europe/London` local time.
- **Severity**: `warning`
- **Processing continues**: Yes.
- **Downstream treatment**: Keep the local timestamp but treat interval ordering and daily charts around the transition with caution.
- **Synthetic example**: A timestamp falls into the repeated hour when clocks go back.

## Daily Reconstruction Check

- **Why it exists**: Compares reconstructed interval totals against each day's final cumulative counter.
- **Severity**: Summary-level validation rather than per-row event.
- **Processing continues**: Yes.
- **Downstream treatment**: Measures with zero mismatches are suitable for dashboard totals. Measures with mismatches remain provisional until reviewed.
- **Synthetic example**: Reconstructed PV intervals total `8.6 kWh`, matching the final daily PV counter of `8.6 kWh`.

## SolaX Reported Consumption

SolaX Plant Reports expose `Daily consumed(kWh)`. Wattson preserves this source
counter as `reported_inverter_consumption_kwh` for diagnostics and data-quality
analysis, but it is not the canonical household-consumption metric.

The current historical dataset indicates that this counter behaves like an
inverter-reported consumption value and appears to include battery or inverter
throughput. It reconciles much more closely with:

```text
reported inverter consumption ~= inverter output - export + import
```

than with the household physical-energy identity:

```text
household_consumption_kwh =
  max(generation_kwh - export_kwh, 0) + grid_import_kwh
```

For dashboard and public aggregate metrics, Wattson derives canonical household
consumption from the physical balance above. The raw inverter-reported counter is
retained separately because it remains useful for diagnosing inverter behaviour,
battery throughput effects and source-data anomalies.

Current reconciled lifetime figures:

| Metric | Value |
| --- | ---: |
| generation | `22861.680 kWh` |
| export | `12420.450 kWh` |
| self-consumed generation | `10441.230 kWh` |
| grid import | `10765.400 kWh` |
| canonical household consumption | `21206.630 kWh` |
| reported inverter consumption | `23897.980 kWh` |
