"""CLI for private tariff what-if comparisons."""

from __future__ import annotations

import argparse
import json

from tariffs.octopus_discovery import discover_relevant_products
from tariffs.real_comparison import run_real_tariff_comparison


def main() -> int:
    parser = argparse.ArgumentParser(description="Wattson private tariff what-if tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("discover-octopus")
    subparsers.add_parser("run-real-comparison")
    args = parser.parse_args()
    if args.command == "discover-octopus":
        products = discover_relevant_products()
        print(
            json.dumps(
                [
                    {
                        "product_code": item.product_code,
                        "display_name": item.display_name,
                        "retrieval_date": item.retrieval_date.isoformat(),
                    }
                    for item in products[:25]
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "run-real-comparison":
        result = run_real_tariff_comparison()
        print(json.dumps(result["public_summary"], indent=2, sort_keys=True, default=str))
        return 0
    return 1
