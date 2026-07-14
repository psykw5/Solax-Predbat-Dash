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
python -m src.live publish-public-snapshot
python -m src.live publish-monthly-summary
python -m src.live publish-website
python -m src.live update-public-dashboard
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

## Private Live Snapshot

`data/public/wattson-live-summary.json` is a local operational artifact only. It may contain delayed live values for trusted local use and must not be published to GitHub Pages.

`publish-public-snapshot` remains a local validation-only command for that operational snapshot. It does not copy, commit or publish live data to GitHub Pages.

## Monthly Public Snapshot

The GitHub Pages website uses only a privacy-first monthly public summary:

```text
data/public/wattson-monthly-summary.json
```

copied to:

```text
../kevinwatson.dev/src/data/wattson-monthly-summary.json
```

The monthly public summary may contain only:

- reporting month;
- publication month;
- lifetime generation;
- lifetime self-consumed energy;
- lifetime export;
- lifetime financial benefit;
- completed-month generation;
- completed-month avoided import value;
- completed-month export income;
- completed-month total benefit;
- nominal and discounted recovery percentages;
- simple and discounted payback months;
- annual summaries;
- high-level data-quality status.

It excludes live PV power, battery state, battery flow, grid flow, today's generation, current or next tariff rates, exact observation timestamps, freshness minutes, raw endpoints, filenames, local paths, raw SolaX inverter-reported consumption and any daily or sub-daily household activity.

Physical energy values in the monthly public summary come from the canonical
SolaX metrics layer. Financial values come from the tariff-covered financial
layer. Missing tariff coverage never removes valid physical generation, export
or self-consumed energy from lifetime physical totals.

Publication is permitted only when:

- the reporting month is complete;
- the selected month has complete settlement half-hour financial data;
- the latest financial calculation has passed validation;
- no public snapshot has already been published for that reporting month;
- the target website repository passes all safety checks.

Recommended timing: run during the first week of each month and publish the completed previous month.

Set `WATTSON_WEBSITE_REPO_PATH` to override the default sibling checkout path.

`publish-monthly-summary` performs the full local website publication:

1. builds and validates the monthly public summary;
2. validates the target website repository;
3. copies only `wattson-monthly-summary.json`;
4. runs the website checks;
5. stages only `src/data/wattson-monthly-summary.json`;
6. commits with `data: update Wattson public snapshot`;
7. pushes to `origin/main`;
8. records the source snapshot hash and website commit hash in the ignored live SQLite store.

The command stops before copying or committing if the website repository is missing, not a Git repository, missing `origin`, not on `main`, dirty, or missing the expected Astro files.

`update-public-dashboard` is the end-to-end safe command for schedulers. It takes a local lock, collects SolaX, refreshes Octopus only when due, builds the private delayed live snapshot for local use, builds the monthly public summary, and publishes the website only when the monthly policy allows it.

Local publication flow:

```powershell
python -m src.live collect-solax
python -m src.live refresh-octopus
python -m src.live build-public-snapshot
python -m src.live publish-monthly-summary
```

The publication command stages, commits and pushes only `src/data/wattson-monthly-summary.json` when all checks pass. Pushing to the website repository's `main` branch triggers its existing GitHub Pages workflow.

For development on Windows, create a Task Scheduler task that runs every 15 minutes:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\path\to\Solax-Predbat-Dash\scripts\wattson-update-public-dashboard.ps1"
```

The command is idempotent and publishes the website at most once for each reporting month.

For a future Raspberry Pi deployment, install the example systemd unit and timer from `scripts/systemd/`, adjust `WorkingDirectory` and `EnvironmentFile`, then run:

```bash
sudo cp scripts/systemd/wattson-public-dashboard.service /etc/systemd/system/
sudo cp scripts/systemd/wattson-public-dashboard.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wattson-public-dashboard.timer
systemctl list-timers wattson-public-dashboard.timer
```

Automated publication should use a GitHub Actions workflow that:

1. checks out this repository;
2. checks out `kevinwatson.dev` into a sibling directory;
3. restores Python dependencies;
4. runs the read-only collectors and snapshot builder;
5. runs `python -m src.live publish-monthly-summary`;
6. commits and pushes only `src/data/wattson-monthly-summary.json` in the website repository when a new reporting month is eligible.

Required automation inputs:

- SolaX and Octopus credentials as repository or environment secrets;
- a cross-repository token with contents read/write access to `kevinwatson.dev`, unless both repositories can be written by the default GitHub token in the chosen setup;
- GitHub Pages enabled on `kevinwatson.dev` with deployment from GitHub Actions.

Do not store repository tokens or API credentials in source code.

## Current Known Field Availability

The SolaX realtime endpoint varies by inverter, dongle firmware and account configuration. The collector accepts common field names for PV power, battery state of charge, battery power, grid power, inverter output, daily yield and total yield. If a field is absent from the response, it is published as `null` rather than guessed.
