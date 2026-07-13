"""Octopus historical tariff discovery and rate ingestion."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ingestion.octopus_client import OctopusClient, build_rates_url, write_sanitized_json
from models.octopus import OctopusAgreement, OctopusRate
from utils.env import require_env_values
from utils.pseudonym import pseudonymize

DEFAULT_RAW_DIR = Path("data/raw/octopus")
DEFAULT_PROCESSED_DIR = Path("data/processed/octopus")
DEFAULT_START = datetime(2023, 1, 24, tzinfo=UTC)
DEFAULT_END = datetime(2026, 7, 12, 23, 59, 59, tzinfo=UTC)
RATE_ENDPOINTS = ("standard-unit-rates", "day-unit-rates", "night-unit-rates")


@dataclass(frozen=True)
class OctopusRunResult:
    agreements: pd.DataFrame
    import_rates: pd.DataFrame
    export_rates: pd.DataFrame
    quality_events: pd.DataFrame


def assert_env_ignored() -> None:
    import subprocess

    result = subprocess.run(
        ["git", "-c", f"safe.directory={Path.cwd().as_posix()}", "check-ignore", "-q", ".env"],
        check=False,
        cwd=Path.cwd(),
    )
    if result.returncode != 0:
        raise RuntimeError(".env is not ignored by Git; refusing to call Octopus API.")


def run_octopus_pipeline(
    raw_dir: Path = DEFAULT_RAW_DIR,
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    period_start: datetime = DEFAULT_START,
    period_end: datetime = DEFAULT_END,
) -> OctopusRunResult:
    assert_env_ignored()
    credentials = require_env_values(["OCTOPUS_API_KEY", "OCTOPUS_ACCOUNT_NUMBER"])
    client = OctopusClient(credentials["OCTOPUS_API_KEY"])
    ingestion_time = datetime.now(UTC)

    account_url, account_payload = client.account(credentials["OCTOPUS_ACCOUNT_NUMBER"])
    write_sanitized_json(raw_dir / "account.json", account_payload)

    agreements = extract_electricity_agreements(
        account_payload, "https://api.octopus.energy/v1/accounts/[account]/", ingestion_time
    )
    agreement_df = pd.DataFrame([agreement.model_dump(mode="json") for agreement in agreements])

    rate_records: list[OctopusRate] = []
    quality_events: list[dict[str, object]] = []
    for agreement in agreements:
        if not overlaps_period(agreement.valid_from, agreement.valid_to, period_start, period_end):
            continue
        query_start = max(agreement.valid_from, period_start)
        query_end = min(agreement.valid_to or period_end, period_end)
        for rate_type in RATE_ENDPOINTS:
            url = build_rates_url(
                agreement.product_code,
                agreement.tariff_code,
                rate_type,
                query_start,
                query_end,
            )
            try:
                raw_rates = client.paged(url)
            except Exception as exc:
                if rate_type == "standard-unit-rates":
                    quality_events.append(
                        quality_event(
                            "rate_endpoint_error",
                            "warning",
                            agreement.direction,
                            agreement.tariff_code,
                            f"{type(exc).__name__}: unable to retrieve {rate_type}",
                        )
                    )
                continue
            write_sanitized_json(
                raw_dir
                / "rates"
                / agreement.direction
                / f"{agreement.agreement_id}_{rate_type}.json",
                {"endpoint": url_without_query(url), "results": raw_rates},
            )
            for raw_rate in raw_rates:
                if raw_rate.get("valid_from") is None:
                    continue
                rate_records.append(
                    OctopusRate(
                        agreement_id=agreement.agreement_id,
                        direction=agreement.direction,
                        tariff_code=agreement.tariff_code,
                        product_code=agreement.product_code,
                        rate_type=rate_type,
                        value_inc_vat=float(raw_rate["value_inc_vat"]),
                        valid_from=parse_datetime(raw_rate["valid_from"]),
                        valid_to=parse_optional_datetime(raw_rate.get("valid_to")),
                        payment_method=raw_rate.get("payment_method"),
                        source_endpoint=url_without_query(url),
                        ingestion_timestamp=ingestion_time,
                    )
                )
    rates_df = pd.DataFrame([rate.model_dump(mode="json") for rate in rate_records])
    quality_df = pd.DataFrame(quality_events)
    write_outputs(processed_dir, agreement_df, rates_df, quality_df)
    return OctopusRunResult(
        agreements=agreement_df,
        import_rates=rates_df[rates_df["direction"] == "import"].copy()
        if not rates_df.empty
        else pd.DataFrame(),
        export_rates=rates_df[rates_df["direction"] == "export"].copy()
        if not rates_df.empty
        else pd.DataFrame(),
        quality_events=quality_df,
    )


def extract_electricity_agreements(
    payload: dict[str, Any], source_endpoint: str, ingestion_time: datetime
) -> list[OctopusAgreement]:
    agreements: list[OctopusAgreement] = []
    for prop_index, property_payload in enumerate(payload.get("properties", [])):
        for mp_index, meter_point in enumerate(
            property_payload.get("electricity_meter_points", [])
        ):
            direction = "export" if meter_point.get("is_export") else "import"
            meter_point_id = pseudonymize(
                f"{prop_index}:{mp_index}:{meter_point.get('mpan', '')}:{direction}",
                "mp",
            )
            for agreement_index, raw_agreement in enumerate(meter_point.get("agreements", [])):
                tariff_code = str(raw_agreement["tariff_code"])
                valid_from = parse_datetime(raw_agreement["valid_from"])
                valid_to = parse_optional_datetime(raw_agreement.get("valid_to"))
                agreement_id = pseudonymize(
                    f"{meter_point_id}:{agreement_index}:{tariff_code}:{valid_from.isoformat()}",
                    "agreement",
                )
                agreements.append(
                    OctopusAgreement(
                        agreement_id=agreement_id,
                        meter_point_id=meter_point_id,
                        direction=direction,
                        tariff_code=tariff_code,
                        product_code=product_code_from_tariff(tariff_code),
                        valid_from=valid_from,
                        valid_to=valid_to,
                        source_endpoint=url_without_query(source_endpoint),
                        ingestion_timestamp=ingestion_time,
                    )
                )
    return agreements


def write_outputs(
    processed_dir: Path,
    agreements: pd.DataFrame,
    rates: pd.DataFrame,
    quality_events: pd.DataFrame,
) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    write_parquet(agreements, processed_dir / "tariff_agreements.parquet")
    write_parquet(
        rates[rates["direction"] == "import"].reset_index(drop=True) if not rates.empty else rates,
        processed_dir / "import_unit_rates.parquet",
    )
    write_parquet(
        rates[rates["direction"] == "export"].reset_index(drop=True) if not rates.empty else rates,
        processed_dir / "export_unit_rates.parquet",
    )
    agreements.to_csv(processed_dir / "tariff_agreements.csv", index=False)
    if not rates.empty:
        rates[rates["direction"] == "import"].to_csv(
            processed_dir / "import_unit_rates.csv", index=False
        )
        rates[rates["direction"] == "export"].to_csv(
            processed_dir / "export_unit_rates.csv", index=False
        )
    quality_events.to_csv(processed_dir / "octopus_ingestion_quality.csv", index=False)


def write_parquet(frame: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, path, compression="snappy")


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_optional_datetime(value: str | None) -> datetime | None:
    return None if value is None else parse_datetime(value)


def product_code_from_tariff(tariff_code: str) -> str:
    parts = tariff_code.split("-")
    if len(parts) < 4:
        raise ValueError(f"Cannot derive product code from tariff code: {tariff_code}")
    return "-".join(parts[2:-1])


def overlaps_period(
    start: datetime, end: datetime | None, period_start: datetime, period_end: datetime
) -> bool:
    return start < period_end and (end is None or end > period_start)


def quality_event(
    event_type: str,
    severity: str,
    direction: str,
    tariff_code: str,
    message: str,
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "severity": severity,
        "direction": direction,
        "tariff_code": tariff_code,
        "message": message,
    }


def url_without_query(url: str) -> str:
    return url.split("?", 1)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Octopus tariff agreements and unit rates.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    args = parser.parse_args()
    try:
        result = run_octopus_pipeline(args.raw_dir, args.processed_dir)
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": "Octopus ingestion failed."}))
        return 1
    print(
        json.dumps(
            {
                "agreements": len(result.agreements),
                "import_rates": len(result.import_rates),
                "export_rates": len(result.export_rates),
                "quality_events": len(result.quality_events),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
