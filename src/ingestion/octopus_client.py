"""Minimal Octopus REST API client with credential-safe behaviour."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from utils.pseudonym import pseudonymize

BASE_URL = "https://api.octopus.energy/v1"


class OctopusClient:
    def __init__(self, api_key: str) -> None:
        token = base64.b64encode(f"{api_key}:".encode()).decode("ascii")
        self._auth_header = f"Basic {token}"

    def get_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"Authorization": self._auth_header})
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def account(self, account_number: str) -> tuple[str, dict[str, Any]]:
        url = f"{BASE_URL}/accounts/{account_number}/"
        return url, self.get_json(url)

    def paged(self, url: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        next_url: str | None = url
        while next_url:
            payload = self.get_json(next_url)
            results.extend(payload.get("results", []))
            next_url = payload.get("next")
        return results


def build_rates_url(
    product_code: str,
    tariff_code: str,
    rate_type: str,
    period_from: datetime,
    period_to: datetime,
) -> str:
    query = urlencode(
        {
            "period_from": period_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period_to": period_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_size": 1500,
        }
    )
    return (
        f"{BASE_URL}/products/{product_code}/electricity-tariffs/{tariff_code}/{rate_type}/?{query}"
    )


def write_sanitized_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sanitize_octopus_payload(payload), indent=2, sort_keys=True), encoding="utf-8"
    )


def sanitize_octopus_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"number", "mpan", "mprn", "serial_number"}:
                sanitized[key] = pseudonymize(item, key_text)
            elif key_text.startswith("address") or key_text in {"postcode", "town", "county"}:
                sanitized[key] = "[redacted]"
            elif key_text in {"id"}:
                sanitized[key] = pseudonymize(item, key_text)
            else:
                sanitized[key] = sanitize_octopus_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_octopus_payload(item) for item in value]
    return value
