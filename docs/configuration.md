# Wattson Configuration

Wattson uses one committed YAML file for stable, non-secret system assumptions:

```text
config/wattson.yaml
```

The file is loaded from the repository root by default. Set `WATTSON_CONFIG_PATH` to point to an
alternative YAML file for a Raspberry Pi deployment, a test run or a future hardware profile.

## Belongs In YAML

- public region and timezone;
- solar array size, panel count, panel rating, roof pitch, orientation and mounting type;
- inverter and battery model family;
- confirmed battery capacity and SoC bounds;
- current public tariff labels;
- public financial assumptions such as installation cost, installation date and opportunity-cost
  discount rate;
- weather provider and public PVGIS assumptions;
- collection/publication intervals and public summary filename.

Unknown YAML keys are rejected. This is intentional: new assumptions should be named and reviewed
rather than slipping into production silently.

## Azimuth Convention

`config/wattson.yaml` uses canonical compass azimuth:

- north: `0` degrees;
- east: `90` degrees;
- south: `180` degrees;
- west: `270` degrees.

External APIs must translate from this canonical convention where required. PVGIS `aspect` uses
south as `0`, east as `-90` and west as `90`, so Wattson converts the configured due-south compass
azimuth of `180` degrees to PVGIS `aspect=0`.

## Belongs In `.env`

Keep private and operational values out of YAML:

- SolaX token and Wi-Fi/dongle serial;
- Octopus API key and account number;
- exact latitude and longitude;
- GitHub or Cloudflare credentials;
- private endpoints.

The YAML may contain `Midlands, UK`; exact coordinates must stay in `.env` as
`WATTSON_LATITUDE` and `WATTSON_LONGITUDE`.

## Validation

The typed loader validates:

- `panel_count * panel_rating_kw == installed_capacity_kwp` within tolerance;
- percentages are between 0 and 100;
- minimum SoC is below maximum SoC;
- optional capacities and power limits are positive when supplied;
- discount rate is between 0 and 1;
- timezone exists;
- public update frequency is one of the supported values;
- public publication cannot enable live household data;
- exact coordinate fields cannot appear anywhere in YAML.

## Nullable Technical Values

These values are deliberately `null` until confirmed:

- battery charge power limit;
- battery discharge power limit;
- battery charge efficiency;
- battery discharge efficiency;
- PVGIS system-loss percentage.

When confirmed from manufacturer documentation, commissioning data or measured calibration, update
the YAML and rerun the relevant pipelines.

## Raspberry Pi Deployment

Use the default `config/wattson.yaml` when the Pi matches the committed assumptions. If the Pi needs
deployment-specific paths or future hardware records, set:

```text
WATTSON_CONFIG_PATH=/path/to/wattson.yaml
```

Do not put credentials or exact coordinates into that YAML. Keep them in the Pi's local `.env` or
service environment.

## Future Replacements And Upgrades

Do not overwrite historical facts when hardware changes. For an inverter replacement, battery
replacement, battery upgrade or extra panels, add dated asset-history records in a future schema so
calculations can use the correct assumptions for each period.
