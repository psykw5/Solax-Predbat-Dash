"""Read-only public Octopus tariff discovery helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

BASE_URL = "https://api.octopus.energy/v1"


@dataclass(frozen=True)
class ProductDiscovery:
    product_code: str
    display_name: str
    is_variable: bool | None
    is_prepay: bool | None
    is_business: bool | None
    retrieval_date: datetime


class PublicOctopusClient:
    def get_json(self, url: str) -> dict[str, Any]:
        with urlopen(url, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def products(self) -> list[ProductDiscovery]:
        payload = self.get_json(f"{BASE_URL}/products/")
        retrieved = datetime.now(UTC)
        return [
            ProductDiscovery(
                product_code=str(item.get("code", "")),
                display_name=str(item.get("display_name", "")),
                is_variable=item.get("is_variable"),
                is_prepay=item.get("is_prepay"),
                is_business=item.get("is_business"),
                retrieval_date=retrieved,
            )
            for item in payload.get("results", [])
        ]

    def product(self, product_code: str) -> dict[str, Any]:
        return self.get_json(f"{BASE_URL}/products/{product_code}/")

    def paged(self, url: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        next_url: str | None = url
        while next_url:
            payload = self.get_json(next_url)
            rows.extend(payload.get("results", []))
            next_url = payload.get("next")
        return rows


def discover_relevant_products(client: PublicOctopusClient | None = None) -> list[ProductDiscovery]:
    octopus = client or PublicOctopusClient()
    products = octopus.products()
    wanted = ("AGILE", "OUTGOING", "FLUX", "VAR", "FLEX")
    return [
        product
        for product in products
        if any(token in product.product_code.upper() for token in wanted)
        or any(token in product.display_name.upper() for token in wanted)
    ]


def tariff_code(product_code: str, region: str) -> str:
    return f"E-1R-{product_code}-{region.upper()}"


def unit_rates_url(
    product_code: str,
    region: str,
    period_from: datetime,
    period_to: datetime,
    rate_type: str = "standard-unit-rates",
) -> str:
    query = urlencode(
        {
            "period_from": period_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period_to": period_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_size": 1500,
        }
    )
    code = tariff_code(product_code, region)
    return f"{BASE_URL}/products/{product_code}/electricity-tariffs/{code}/{rate_type}/?{query}"


def standing_charges_url(
    product_code: str,
    region: str,
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
    code = tariff_code(product_code, region)
    return (
        f"{BASE_URL}/products/{product_code}/electricity-tariffs/{code}/standing-charges/?{query}"
    )


def write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
