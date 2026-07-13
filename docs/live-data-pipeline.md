# Live Data Collection and Public Snapshot Pipeline

This document describes Wattson's read-only live collection path. It does not include any battery-control or inverter-write functionality.

## Credentials

Live collection reads credentials only from environment variables, with local `.env` loading supported for development:

- `SOLAX_TOKEN_ID`;
- `SOLAX_WIFI_SN`;
- `OCTOPUS_API_KEY`;
- `OCTOPUS_ACCOUNT_NUMBER`;
- `PUBLIC_DATA_DELAY_MINUTES`, default `30`.

Credential values must never be printed, logged, persisted or committed. `.env`, raw API responses, processed private datasets, SQLite databases and generated public JSON snapshots are ignored by Git.

## Commands

Run from the repository root:

```powershell
python -m src.live collect-solax
python -m src.live refresh-octopus
python -m src.live build-public-snapshot
python -m src.live run
```

`run` collects SolaX telemetry, refreshes Octopus tariffs when the previous successful tariff refresh is at least 24 hours old, and publishes a public snapshot only when mandatory validation passes.

## Recommended Schedule

- SolaX live collection: every 15 minutes.
- Octopus tariff refresh: daily.
- Full financial rebuild: daily after successful tariff refresh and historical data processing.
- Public snapshot: after successful collection and validation.

At that schedule, the expected SolaX request count is about `96` requests per day. The expected Octopus request count is one account call plus current/upcoming rate calls for active import and export agreements, normally about `7` requests per day when both import and export have standard/day/night endpoints. The implementation does not redownload the complete historical tariff record on every live run.

## SolaX Telemetry

The collector uses the SolaX Cloud user-monitoring realtime endpoint in read-only mode and normalises available fields:

- observation timestamp;
- PV power;
- battery state of charge;
- battery charge or discharge power;
- grid import or export power;
- inverter output;
- daily generation;
- cumulative generation.

Malformed, future-dated or stale responses are rejected. A failed collection records a data-quality event and the previous valid observation remains available for public snapshot generation.

Raw SolaX responses are preserved only under ignored local storage with serial numbers, Wi-Fi identifiers and token-like fields redacted or pseudonymised.

## Octopus Tariffs

The live tariff refresh reuses the existing Octopus REST client and agreement parsing. It retrieves the active import and export agreements, current applicable rates, and upcoming rates where the API returns them.

Agreement and active-rate refresh runs daily. If the active product code changes compared with the previous live snapshot, Wattson records a warning that the complete historical tariff backfill should be run.

## SQLite Persistence

Operational live data is stored in a local SQLite database under `data/live/`, separate from Home Assistant's database. The database stores:

- normalised SolaX observations;
- Octopus tariff snapshots;
- collector-run metadata;
- data-quality events.

SolaX inserts are idempotent by observation timestamp. Tariff snapshot inserts are idempotent by direction, tariff code, validity start and capture time.

## Public Snapshot

The public snapshot is written to:

```text
data/public/wattson-live-summary.json
```

It contains only high-level, public-safe values:

- generated time;
- delayed data-as-of time;
- current PV power;
- current battery percentage, direction and power;
- current grid direction and power;
- today's generation;
- current import and export rates;
- next tariff change and next rate where known;
- confirmed lifetime financial benefit;
- nominal and discounted recovery percentages;
- simple and discounted payback months;
- health status;
- freshness in minutes.

It does not include account numbers, MPANs, meter serials, Wi-Fi identifiers, addresses, coordinates, raw API responses, detailed household intervals, private endpoints, credential values, filenames or local paths.

## Privacy Delay

`PUBLIC_DATA_DELAY_MINUTES` defaults to `30`. The public snapshot selects the newest valid SolaX observation at least that many minutes old. Negative delay values are rejected.

## Current Known Field Availability

The SolaX realtime endpoint varies by inverter, dongle firmware and account configuration. The collector accepts common field names for PV power, battery state of charge, battery power, grid power, inverter output, daily yield and total yield. If a field is absent from the response, it is published as `null` rather than guessed.
