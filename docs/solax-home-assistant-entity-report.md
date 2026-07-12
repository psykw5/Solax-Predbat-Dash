# SolaX Home Assistant Entity Report

Report date: 2026-07-12

Scope: connect Home Assistant to the SolaX inverter in read-only mode, enumerate entities exposed by the Home Assistant SolaX integration, and identify which entities are suitable for historical storage.

## Result

Home Assistant could not be connected to the SolaX inverter during this pass.

No SolaX entities are currently exposed in Home Assistant, so there are no entity IDs, friendly names, units, current values, or observed update timestamps to enumerate yet.

## Connection Attempt

Target supplied:

- Inverter/dongle IP: `192.168.1.112`
- Home Assistant integration: built-in `solax`
- Integration name: SolaX Power
- Integration class: local polling
- Attempted port: `80`
- Attempted password: blank/default
- Write/control functionality: not enabled

Observed Home Assistant integration behaviour:

- The built-in SolaX integration requires IP address, port, and optional password.
- The default port is `80`.
- The integration creates sensor entities only after it successfully reads real-time data from the inverter.
- The installed integration polling interval is `30 seconds`.
- The integration exposes sensors only; no control entities were inspected or enabled.

Read-only probe result from inside the Home Assistant container:

```text
solax.discovery.DiscoveryError: Unable to connect to the inverter at host=192.168.1.112 port=80
```

Windows host reachability checks:

```text
192.168.1.112:80  ping succeeded, TCP connect failed
192.168.1.112:502 ping succeeded, TCP connect failed
```

Interpretation:

- The IP address responds at the network level.
- The local SolaX HTTP endpoint expected by Home Assistant is not reachable on port `80` from this machine/container.
- Modbus TCP is not reachable on port `502` from this machine.
- This may mean the SolaX dongle local API is disabled, blocked, on a different port/interface, password-gated in a way that prevents discovery, or the IP belongs to a device that does not expose the expected SolaX local API.

## Current Home Assistant SolaX Entities

No SolaX config entry exists in Home Assistant at the time of this report.

| Entity ID | Friendly name | Unit | Update frequency | Current value | Historical storage suitability |
| --- | --- | --- | --- | --- | --- |
| None observed | None observed | None observed | Not observable | Not observable | Cannot assess until integration connects |

## Entity Types Expected After Connection

The built-in integration builds entities dynamically from the inverter's reported sensor map. Until the inverter responds, the exact list cannot be known.

Based on the installed integration's sensor descriptions, possible entity classes include:

| Sensor class | Unit | State class | Historical storage suitability |
| --- | --- | --- | --- |
| Energy | `kWh` | `total_increasing` | High, if counters map to PV generation, grid import/export, battery charge/discharge, or load energy |
| Power | `W` | `measurement` | High, useful for interval aggregation and live monitoring |
| Battery state of charge | `%` | `measurement` | High, useful for battery performance and optimisation context |
| Temperature | `°C` | `measurement` | Medium, useful for diagnostics and quality checks |
| Voltage | `V` | `measurement` | Low to medium, useful for diagnostics rather than MVP savings |
| Current | `A` | `measurement` | Low to medium, useful for diagnostics rather than MVP savings |
| Frequency | `Hz` | `measurement` | Low, useful for diagnostics |
| Unclassified value | none | none | Case-by-case; only store after mapping is understood |

## Most Suitable Entities for Historical Storage

Once the integration connects, prioritise entities in this order:

1. PV generation energy, `kWh`.
2. PV generation power, `W`.
3. Grid import energy, `kWh`.
4. Grid export energy, `kWh`.
5. Grid import/export power, `W`.
6. Battery state of charge, `%`.
7. Battery charge energy, `kWh`.
8. Battery discharge energy, `kWh`.
9. Battery charge/discharge power, `W`.
10. Household/load energy or power, if exposed.
11. Inverter and battery temperatures for diagnostics.

Diagnostic-only entities such as voltage, current, frequency, firmware, operating mode, and status values should not be part of MVP savings calculations unless they are needed for data quality checks.

## Storage Guidance

For historical storage:

- Store `kWh` entities with `total_increasing` state as cumulative counters and derive interval deltas.
- Store `W` entities as sampled power measurements and aggregate to minute or half-hour intervals.
- Store battery SoC as sampled state.
- Preserve Home Assistant entity metadata: entity ID, friendly name, unit, device class, state class, and source integration.
- Preserve raw current values and attributes during early ingestion until the final entity mapping is trusted.
- Treat same-day SolaX readings as provisional until reconciled with Octopus import/export readings.

## Next Required Action

Resolve why the local SolaX endpoint is not reachable.

Checks to perform:

- Confirm the IP `192.168.1.112` is the SolaX inverter/dongle, not another network device.
- Confirm whether the dongle exposes a local HTTP API on port `80`.
- Confirm whether the dongle requires a non-blank password.
- Confirm whether local API/LAN access is disabled in the SolaX dongle settings.
- Confirm whether the inverter/dongle supports Modbus TCP and whether port `502` is enabled.
- If using PocketWiFi, confirm whether local access is only available through the dongle's own access point rather than the home LAN.

## Control Safety

No control functionality was enabled.

No automations were modified.

No write calls were made to the inverter.

No SolaX config entry was created because the read-only first data refresh could not succeed.
