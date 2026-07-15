# Weather and Solar-Forecast Data Layer

This layer collects weather forecasts, reanalysis observations and PVGIS solar benchmarks for future private modelling. It does not implement forecasting models, recommendations, optimisation, public weather dashboards or inverter control.

## Private Configuration

The installation is represented locally as:

- array capacity, panel count, panel rating, orientation and roof pitch from
  `config/wattson.yaml`;
- significant shading: none;
- public region: `Midlands, UK`.

Exact latitude and longitude are read only from `.env`:

```text
WATTSON_LATITUDE=
WATTSON_LONGITUDE=
```

They must never be printed, logged, committed or published. Public outputs may use only `Midlands, UK`.

## Sources

Open-Meteo forecast requests use the official forecast endpoint with hourly irradiance, cloud, temperature, humidity, precipitation, wind and weather-code variables. Forecast vintages are preserved by storing both retrieval/issue time and target time.

Open-Meteo historical archive requests are stored separately as reanalysis weather. They must not be described as direct local observations. Historical forecasts, previous model runs and satellite radiation are modelled as separate datasets so future backfills do not overwrite forecast-as-issued records.

PVGIS is used as a European Commission/JRC physics-based benchmark with fixed mounting and the configured array assumptions. PVGIS system-loss percentage is deliberately `null` until confirmed, so Wattson omits that parameter rather than inventing a value. PVGIS is a benchmark, not ground truth.

## Storage

SQLite operational tables:

- `weather_forecast_run`;
- `weather_forecast_interval`;
- `weather_observation_interval`;
- `solar_radiation_observation`;
- `pvgis_baseline`;
- `forecast_quality_event`.

Sanitised raw responses are written under ignored paths:

- `data/raw/weather/open_meteo/`;
- `data/raw/weather/pvgis/`.

Canonical Parquet exports are written under ignored `data/processed/weather/`.

## Commands

```powershell
python -m src.weather collect-forecast
python -m src.weather collect-observations
python -m src.weather backfill-history
python -m src.weather build-pvgis-baseline
python -m src.weather build-evaluation-dataset
python -m src.weather evaluate-baselines
python -m src.weather run
```

Recommended schedule:

- forecast retrieval every 3 hours;
- observation/reanalysis refresh daily;
- forecast evaluation daily after actual generation is complete;
- historical backfill manually.

The monthly public website publication schedule is unchanged.

## Evaluation Foundations

The validation-ready dataset joins hourly weather rows to SolaX generation and includes actual generation, forecast irradiance, observed or reanalysis irradiance, cloud variables, temperature, daylight indicator, forecast issue time, lead hours, source/model and data-quality status.

Initial transparent baselines are:

- calendar-month historical average;
- recent-day persistence;
- PVGIS expected generation;
- irradiance-scaled capacity.

Forecast evaluation reports MAE, RMSE, mean bias error, safe-threshold percentage error, daily forecast-versus-actual totals and forecast revision magnitude. It deliberately does not report a single confidence value.
