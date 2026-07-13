"""Read-only SolaX Cloud live telemetry collection."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from live.config import DEFAULT_RAW_LIVE_DIR, SOLAX_KEYS, require_credentials
from live.models import SolaXObservation
from utils.pseudonym import pseudonymize

SOLAX_REALTIME_URL = "https://www.solaxcloud.com/proxyApp/proxy/api/getRealtimeInfo.do"
MAX_STALENESS_MINUTES = 60
SOLAX_LOCAL_TZ = ZoneInfo("Europe/London")


def collect_solax_observation(
    raw_dir: Path = DEFAULT_RAW_LIVE_DIR / "solax",
    now: datetime | None = None,
) -> SolaXObservation:
    credentials = require_credentials(SOLAX_KEYS)
    payload = fetch_solax_payload(credentials["SOLAX_TOKEN_ID"], credentials["SOLAX_WIFI_SN"])
    received_at = now or datetime.now(UTC)
    write_redacted_raw(raw_dir, payload, received_at)
    return normalize_solax_payload(payload, received_at)


def fetch_solax_payload(token_id: str, wifi_sn: str) -> dict[str, Any]:
    query = urlencode({"tokenId": token_id, "sn": wifi_sn})
    request = Request(f"{SOLAX_REALTIME_URL}?{query}")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_solax_payload(
    payload: dict[str, Any],
    received_at: datetime,
    max_staleness: timedelta = timedelta(minutes=MAX_STALENESS_MINUTES),
) -> SolaXObservation:
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise ValueError("SolaX response does not contain a result object.")
    success = payload.get("success", True)
    code = payload.get("code")
    if success is False or payload.get("errorCode") or code not in (None, 0, "0"):
        raise ValueError("SolaX response reports an API error.")

    observation_time = parse_solax_timestamp(
        first_present(result, ["uploadTime", "utcDateTime", "timestamp", "time"])
    )
    if observation_time is None:
        raise ValueError("SolaX response is missing an observation timestamp.")
    if observation_time.tzinfo is None:
        observation_time = observation_time.replace(tzinfo=UTC)
    observation_time = observation_time.astimezone(UTC)
    if observation_time > received_at + timedelta(minutes=5):
        raise ValueError("SolaX response timestamp is in the future.")
    if received_at - observation_time > max_staleness:
        raise ValueError("SolaX response is stale.")

    battery_power = number(first_present(result, ["batPower", "batteryPower", "batPowerNow"]))
    grid_power = number(
        first_present(result, ["feedinPower", "feedinpower", "gridPower", "gridpower"])
    )
    return SolaXObservation(
        observation_timestamp=observation_time,
        received_at=received_at,
        pv_power_kw=kw(first_present(result, ["acpower", "pvPower", "powerdc1", "powerdc2"])),
        battery_soc_percent=number(first_present(result, ["soc", "batterySoc", "batCapacity"])),
        battery_power_kw=kw_abs(battery_power),
        battery_direction=direction_from_power(battery_power, "charge", "discharge"),
        grid_power_kw=kw_abs(grid_power),
        grid_direction=direction_from_power(grid_power, "export", "import"),
        inverter_output_kw=kw(
            first_present(result, ["inverterOutputPower", "outputPower", "acpower"])
        ),
        daily_generation_kwh=number(
            first_present(result, ["yieldtoday", "todayYield", "energyToday"])
        ),
        cumulative_generation_kwh=number(
            first_present(result, ["yieldtotal", "totalYield", "energyTotal"])
        ),
    )


def parse_solax_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("/", "-")
    if text.endswith("Z"):
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=SOLAX_LOCAL_TZ) if parsed.tzinfo is None else parsed
        except ValueError:
            continue
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=SOLAX_LOCAL_TZ) if parsed.tzinfo is None else parsed


def first_present(payload: dict[str, Any], names: list[str]) -> object:
    for name in names:
        if name in payload and payload[name] not in ("", None):
            return payload[name]
    return None


def number(value: object) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def kw(value: object) -> float | None:
    parsed = number(value)
    if parsed is None:
        return None
    return round(parsed / 1000 if abs(parsed) > 100 else parsed, 3)


def kw_abs(value: float | None) -> float | None:
    return (
        None if value is None else round(abs(value) / 1000 if abs(value) > 100 else abs(value), 3)
    )


def direction_from_power(value: float | None, positive: str, negative: str) -> str | None:
    if value is None:
        return None
    if value > 0:
        return positive
    if value < 0:
        return negative
    return "idle"


def write_redacted_raw(raw_dir: Path, payload: dict[str, Any], received_at: datetime) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe = redact_solax_payload(payload)
    filename = f"solax_realtime_{received_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    (raw_dir / filename).write_text(json.dumps(safe, indent=2, sort_keys=True), encoding="utf-8")


def redact_solax_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"sn", "wifisn", "registrationno", "invertersn", "tokenid"}:
                redacted[key] = pseudonymize(item, key_text)
            else:
                redacted[key] = redact_solax_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_solax_payload(item) for item in value]
    return value
