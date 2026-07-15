# Tariff What-If Comparison Framework

This private framework compares the current Octopus Flux baseline with alternative tariff combinations. It does not switch tariffs, control the inverter, publish results or update the website.

## Initial Scenarios

| Scenario | Import tariff | Export tariff | Initial eligibility |
|---|---|---|---|
| `octopus_flux` | Flux import | Flux export | `eligible` for solar and battery owners where Octopus account requirements are met |
| `agile_import_agile_outgoing` | Agile Octopus | Agile Outgoing | `compatibility_unconfirmed` |
| `agile_import_flat_outgoing` | Agile Octopus | Outgoing Octopus fixed export | `compatibility_unconfirmed` |
| `standard_import_prime_outgoing` | Flexible/standard import | Prime Outgoing export | `compatibility_unconfirmed` |

Intelligent Octopus Flux can be recorded later as an unavailable benchmark, but it is not an actionable scenario until current official compatibility supports the installed SolaX system.

`intelligent_octopus_go_future_ev` is a future private scenario marker only. It is not ranked
or published until EV charging can be separated from household demand and charger/vehicle
compatibility has been confirmed. Private EV charging times, identifiers and behaviour must
never be included in public outputs.

## Official Evidence

Octopus Flux is described by Octopus as a three-rate import/export combination tariff for solar and battery owners, with smart-meter, solar, battery and Octopus import/export eligibility requirements.

Agile Octopus is an import smart tariff with half-hourly prices updated daily and a standing charge.

Outgoing Octopus provides export tariffs for exported solar energy. Agile Outgoing and flat export rates must be treated as separate export products.

Pairing eligibility must be stored as evidence. Wattson must not assume that any import tariff can be paired with any export tariff.

## Comparison Basis

`historical_as_available` means the calculation uses only tariffs and rates that were actually
active for the home at the time.

`current_tariff_repriced_history` means measured half-hourly energy flows are repriced using
current tariff products and current official rate data over the same measured period. This is the
default basis for the public tariff comparison because it gives a fair like-for-like replay without
pretending a historic tariff switch happened.

`forward_projection` means a future estimate. It must be clearly labelled and must not be mixed
with measured replay results.

## Strategies

`actual_flow_replay` prices measured half-hourly grid import and export without altering battery behaviour.

`optimised_battery_simulation` simulates a configurable 6 kWh battery with minimum/maximum SoC, charge/discharge power limits, efficiency and explicit controls for grid charging and battery export. Simulated flows are stored separately from measured flows.

The optimisation is experimental. It can show directional opportunity, but it is not evidence that
the real inverter would have behaved that way and it must not imply any inverter control has been
enabled.

## Cost Definition

`net electricity cost = import energy cost + standing charges - export income`

The complete tariff comparison includes standing charges. Gas, finance, maintenance, battery degradation and hardware replacement are excluded.

## Fair Comparison

Scenarios must use the same energy coverage, missing-data exclusions, VAT convention, battery constraints and start/end period. Scenarios with incomplete tariff coverage are reported separately and not ranked unfairly.

## Private Outputs

Ignored outputs are written under `data/processed/tariff_whatif/`:

- half-hourly simulated results;
- scenario summary CSV;
- comparison JSON for the future private dashboard.

No tariff what-if output is public.

## Public-Safe Summary

The optional generated public artifact is `data/public/wattson-tariff-comparison.json`. It is
Git-ignored and contains only aggregate scenario rows:

- reporting months and half-hour count;
- scenario display name and eligibility status;
- comparison basis;
- aggregate net cost and difference versus Flux;
- aggregate coverage and data-quality status;
- separate experimental optimisation aggregates.

It must not contain account numbers, MPANs, serial numbers, meter identifiers, coordinates,
filenames, endpoints, private EV details, exact household activity intervals or raw API payloads.

## Website Presentation Proposal

The website should present this as a privacy-first "Tariff what-if" card below the existing Wattson
monthly summary:

- Start with the Flux baseline and explain that alternatives are repriced against measured
  household energy flows.
- Show a compact table with scenario, eligibility, coverage, net cost, difference versus Flux and
  rank where a scenario is confirmed comparable.
- Keep compatibility-unconfirmed pairings visually separate from ranked results.
- Add a secondary "experimental optimisation" section with battery throughput/cycle aggregates,
  clearly labelled as simulation rather than measured behaviour.
- Avoid live rates, exact timestamps, half-hourly traces or private household activity charts.

The website repository is not modified by this pipeline.
