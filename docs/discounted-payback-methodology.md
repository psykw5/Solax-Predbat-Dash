# Discounted Payback and NPV Methodology

This document describes Wattson's discounted payback and NPV projection layer.

## Scope

The model uses:

- installation cost: `13000 GBP`;
- annual discount or opportunity-cost rate: `5%`;
- confirmed historical monthly benefits from the SolaX and Octopus financial pipeline;
- no panel, inverter or battery degradation assumption;
- no electricity-price inflation assumption;
- no private financing information.

The model does not include personal loans, mortgage rates, deposits, repayment history, standing charges, system maintenance or replacement costs unless they are explicitly added later as dated capital cash-flow events.

## Installation Date

The current default installation and benefit-start date is `2023-01-24`, matching the start of the validated SolaX historical dataset. If a more precise commissioning date is later confirmed, it should be moved into configuration and the projection regenerated.

## Historical Cash Flows

Monthly historical cash flows are read from `data/processed/financial/monthly_financial_summary.csv`.

For each confirmed historical month, Wattson uses:

- avoided import value;
- export income;
- total financial benefit.

Monthly benefits are treated as received at month end. Each monthly benefit is discounted back to the installation date using the effective monthly rate derived from the annual discount rate:

`monthly_rate = (1 + annual_rate) ** (1 / 12) - 1`

## Current Recovery and NPV

The current nominal recovery percentage is:

`confirmed historical nominal benefit / installation cost`

The current discounted recovery percentage is:

`discounted historical benefit / installation cost`

Current NPV is:

`discounted historical benefit - installation cost`

## Future Projection

Future benefits use a measured seasonal monthly profile:

1. Group confirmed historical benefits by calendar month.
2. Average each calendar month's confirmed benefit across available historical years.
3. Project future months using that month-specific average.

Tariffs and system performance are held constant in real terms. The projection does not assume degradation or electricity-price inflation.

Projection continues until discounted payback is reached or 25 years from installation, whichever comes first.

## Future Capital Events

The model supports future dated capital cash-flow events, for example:

- inverter replacement;
- battery replacement;
- battery upgrade;
- additional panels.

No hypothetical replacement or upgrade costs are included by default. When real costs occur, they should be added as dated negative cash-flow events and the payback projection should be regenerated.

## Outputs

Generated local outputs under `data/processed/financial/`:

- `payback_projected_cash_flows.parquet`;
- `payback_projected_cash_flows.csv`;
- `payback_annual_summary.csv`;
- `payback_public_summary.json`.

These files contain aggregate modelling results only and no private identifiers.
