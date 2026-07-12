# Solax Predbat Dash

Initial Home Assistant Container development environment for Docker Desktop on Windows.

## Validate

```powershell
docker compose config
```

## Start

```powershell
docker compose up -d
```

## Stop

```powershell
docker compose down
```

## Logs

```powershell
docker compose logs -f homeassistant
```

## Access

Open Home Assistant at http://localhost:8123.

The Home Assistant configuration is persisted in `./config`. This setup uses bridge networking with port `8123` published for Docker Desktop on Windows.

## Historical SolaX ETL

Place SolaX Plant Report `.xlsx` files under `data/raw/solax/`. Raw and processed data are ignored by Git.

Run the ETL:

```powershell
$env:PYTHONPATH='src'
python -m ingestion.solax_pipeline
```

Generate the dataset coverage and validation report:

```powershell
$env:PYTHONPATH='src'
python -m metrics.reporting
```

Processed outputs are written to `data/processed/solax/`, including Parquet, CSV, validation reports, ingestion summary, monthly metrics, annual metrics, and the generated markdown dataset report.

## Metrics

Use the read-only metrics API against the processed Parquet:

```python
from metrics import EnergyMetrics

metrics = EnergyMetrics()
metrics.total_generation("2023-01-01", "2024-01-01")
metrics.monthly_summary(2024, 1)
metrics.annual_summary(2024)
```

## Quality Checks

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -p "test_*.py"
ruff check .
ruff format --check .
pyright
pre-commit run --all-files
```

## Project Roadmap

This project aims to become a production-quality home energy optimisation platform. The system should evolve as a set of clearly separated components so each part can be developed, tested, deployed, and replaced independently.

### Architecture Areas

- **Home Assistant**: Local integration hub for devices, sensors, automations, and safe control surfaces.
- **Data collection**: Reliable ingestion, normalisation, validation, and storage of inverter, battery, solar, grid, tariff, weather, and household consumption data.
- **Optimisation engine**: Forecasting and decision logic for battery charge, discharge, import, export, and load-shifting strategies.
- **Dashboard**: Operator-facing views for live status, historical trends, forecasts, recommendations, simulations, and control decisions.
- **Testing**: Unit, integration, simulation, regression, and safety tests covering data quality, optimisation behaviour, and Home Assistant interactions.
- **Documentation**: Setup guides, architecture decisions, operating procedures, data contracts, automation notes, and safety constraints.

### Phases

1. **Monitoring**
   - Establish trustworthy Home Assistant connectivity and baseline entity discovery.
   - Collect and persist core energy data: solar generation, battery state, grid import/export, load, tariffs, and weather inputs.
   - Build initial dashboard views for live state, daily totals, and obvious data gaps.
   - Add validation checks for missing, stale, or inconsistent readings.

2. **Recommendation**
   - Produce human-readable optimisation recommendations without taking control actions.
   - Forecast short-term generation, demand, battery state, and tariff cost windows.
   - Explain each recommendation with expected cost, comfort, battery, and risk impact.
   - Compare recommendations against actual outcomes to improve model quality.

3. **Simulation**
   - Add a simulation engine for replaying historical days and testing future scenarios.
   - Evaluate optimisation strategies before they are allowed near live control.
   - Track metrics such as cost saving, self-consumption, export value, battery cycling, and failure modes.
   - Use regression scenarios to prevent changes from degrading established behaviours.

4. **Autonomous Control**
   - Introduce guarded Home Assistant control actions with explicit safety limits and manual override.
   - Start with low-risk scheduled actions, then expand only after simulation and recommendation evidence is strong.
   - Log every decision, input, action, and fallback for auditability.
   - Provide operational dashboards for current mode, active constraints, upcoming actions, and intervention history.

## Release Roadmap

- **v0.1 Historical SolaX ingestion and metrics**
- **v0.2 Read-only dashboard**
- **v0.3 Octopus tariff and meter integration**
- **v0.4 Financial savings calculations**
- **v0.5 Live collection**
- **v0.6 Recommendation and simulation**
- **v1.0 Guarded autonomous control**
